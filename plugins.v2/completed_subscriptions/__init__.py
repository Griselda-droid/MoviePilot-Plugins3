# -*- coding: utf-8 -*-

"""
*************************************************
***      已完成订阅查看器 (CompletedSubscriptions)     ***
*************************************************
- 功能：定时获取所有状态为“已完成”的订阅，并在日志中显示订阅的媒体和用户。
- 作者：Gemini & 用户
- 规范：严格参照 SubscribeAssistant 插件结构编写。
"""

from typing import Any, Dict, List, Tuple
from apscheduler.triggers.cron import CronTrigger

# 从 app 核心模块导入必要的类
from app.log import logger
from app.plugins import _PluginBase
from app.helper.subscribe import SubscribeHelper
from app.helper.user import UserHelper
from app.schemas import NotificationType
from app.utils.timer import TimerUtils


# 插件主类，类名严格遵循“文件夹名驼峰化”的规则
class CompletedSubscriptions(_PluginBase):
    # 插件元信息，参照 SubscribeAssistant 规范
    plugin_name = "已完成订阅查看器"
    plugin_desc = "定时获取所有已完成的订阅，并清晰地展示订阅的媒体以及对应的用户。"
    plugin_icon = "https://raw.githubusercontent.com/InfinityPacer/MoviePilot-Plugins/main/icons/completed.png"
    plugin_version = "1.2.0"
    plugin_author = "Gemini & 用户"
    author_url = "https://github.com/InfinityPacer/MoviePilot-Plugins"
    plugin_config_prefix = "completed_subs_"
    auth_level = 1

    # 私有属性，用于存储配置和状态
    _enabled = False
    _notify = False
    _cron = None
    _onlyonce = False

    # 插件核心辅助类的实例
    subscribe_helper: SubscribeHelper = None
    user_helper: UserHelper = None

    def init_plugin(self, config: dict = None):
        """
        插件初始化，在系统启动或插件配置更新时调用。
        """
        # 实例化辅助类
        self.subscribe_helper = SubscribeHelper()
        self.user_helper = UserHelper()

        # 读取并应用配置
        if config:
            self._enabled = config.get("enabled", False)
            self._notify = config.get("notify", False)
            self._cron = config.get("cron")
            self._onlyonce = config.get("onlyonce", False)

        # 如果设置了“立即运行一次”，则立即执行并重置开关
        if self._onlyonce:
            logger.info(f"【{self.plugin_name}】：配置了“立即运行一次”，任务即将开始...")
            self.run_check()
            self._onlyonce = False
            self.__update_config()

    def get_state(self) -> bool:
        """
        返回插件的启用状态，决定插件服务是否注册。
        """
        return self._enabled

    def get_service(self) -> List[Dict[str, Any]]:
        """
        向 MoviePilot 系统注册后台服务。
        """
        if not self.get_state():
            return []

        # 如果用户自定义了 CRON 表达式
        if self._cron:
            return [{
                "id": f"{self.__class__.__name__}_check",
                "name": "已完成订阅检查",
                "trigger": CronTrigger.from_crontab(self._cron),
                "func": self.run_check,
                "kwargs": {}
            }]
        # 如果没有定义，则每天凌晨随机时间执行一次
        else:
            random_trigger = TimerUtils.random_scheduler(num_executions=1, begin_hour=2, end_hour=5)[0]
            return [{
                "id": f"{self.__class__.__name__}_check_random",
                "name": "已完成订阅检查 (随机)",
                "trigger": "cron",
                "func": self.run_check,
                "kwargs": {"hour": random_trigger.hour, "minute": random_trigger.minute}
            }]

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        定义并返回插件的配置界面（V-Form）和数据模型。
        """
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol', 'props': {'cols': 12, 'md': 6},
                                'content': [
                                    {'component': 'VSwitch', 'props': {'model': 'enabled', 'label': '启用插件', 'hint': '开启或关闭插件功能', 'persistent-hint': True}}
                                ]
                            },
                            {
                                'component': 'VCol', 'props': {'cols': 12, 'md': 6},
                                'content': [
                                    {'component': 'VSwitch', 'props': {'model': 'notify', 'label': '发送通知', 'hint': '任务执行后发送通知消息', 'persistent-hint': True}}
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol', 'props': {'cols': 12, 'md': 12},
                                'content': [
                                    {'component': 'VCronField', 'props': {'model': 'cron', 'label': '执行周期 (CRON)', 'hint': '留空则每日凌晨随机执行，例如: 0 3 * * *', 'persistent-hint': True}}
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol', 'props': {'cols': 12, 'md': 6},
                                'content': [
                                    {'component': 'VSwitch', 'props': {'model': 'onlyonce', 'label': '保存后立即运行一次', 'hint': '该开关会在执行后自动关闭', 'persistent-hint': True}}
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow', 'content': [
                             {'component': 'VCol', 'props': {'cols': 12},
                              'content': [
                                  {'component': 'VAlert', 'props': {'type': 'info', 'variant': 'tonal', 'text': '此插件用于扫描所有已完成下载的订阅，并在日志中输出一个清晰的列表，显示每个媒体是由哪个用户订阅的。'}}
                              ]}
                        ]
                    }
                ]
            }
        ], {
            "enabled": False,
            "notify": False,
            "cron": "",
            "onlyonce": False
        }

    def stop_service(self):
        """
        插件停止时调用的方法，用于清理。
        """
        self._enabled = False

    def run_check(self):
        """
        插件的核心执行逻辑。
        """
        logger.info(f"开始执行【{self.plugin_name}】任务...")

        try:
            # 1. 获取所有订阅
            all_subscriptions = self.subscribe_helper.get_all()
            if not all_subscriptions:
                logger.info(f"【{self.plugin_name}】：数据库中没有任何订阅记录。")
                return

            # 2. 筛选出状态为 "Downloaded" (已完成) 的订阅
            completed_subs = [sub for sub in all_subscriptions if sub.status == 'Downloaded']

            if not completed_subs:
                logger.info(f"【{self.plugin_name}】：未找到状态为“已完成”的订阅。")
                return

            logger.info(f"【{self.plugin_name}】：成功找到 {len(completed_subs)} 个已完成的订阅，正在整理输出...")

            # 3. 准备输出内容
            output_lines = ["", f"--- [ {self.plugin_name} - 扫描结果 ] ---"]
            for sub in completed_subs:
                title = sub.get_title()
                user_name = "未知用户"
                if sub.user_id:
                    user_info = self.user_helper.get(sub.user_id)
                    if user_info and user_info.name:
                        user_name = user_info.name
                
                output_lines.append(f"  - 媒体: {title}")
                output_lines.append(f"  - 用户: {user_name}")
                output_lines.append("  ---------------------------------")
            
            result_text = "\n".join(output_lines)
            
            # 4. 输出到日志
            logger.info(result_text)

            # 5. 如果开启了通知，则发送消息
            if self._notify:
                self.post_message(
                    mtype=NotificationType.Plugin,
                    title=f"【{self.plugin_name}】执行完成",
                    text=f"扫描完成，共发现 {len(completed_subs)} 个已完成的订阅。详情请查看插件日志。"
                )

            logger.info(f"【{self.plugin_name}】任务执行完毕。")

        except Exception as e:
            logger.error(f"执行【{self.plugin_name}】插件时发生未知错误: {e}", exc_info=True)

    def __update_config(self):
        """
        一个私有辅助方法，用于将当前配置保存回数据库。
        """
        self.update_config({
            "enabled": self._enabled,
            "notify": self._notify,
            "cron": self._cron,
            "onlyonce": self._onlyonce,
        })