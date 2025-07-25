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

from app.log import logger
from app.plugins import _PluginBase
from app.db.subscribe_oper import SubscribeOper
from app.schemas import NotificationType

class CompletedSubscriptions(_PluginBase):
    # 插件元信息
    plugin_name = "已完成订阅查看器"
    plugin_desc = "定时获取所有已完成的订阅，并清晰地展示订阅的媒体以及对应的用户。"
    plugin_icon = "https://raw.githubusercontent.com/InfinityPacer/MoviePilot-Plugins/main/icons/subscribeassistant.png"
    plugin_version = "2.1.0" # 修正了目录名和属性错误
    plugin_author = "Gemini & 用户"
    author_url = "https://github.com/InfinityPacer/MoviePilot-Plugins"
    plugin_config_prefix = "completed_subs_"
    auth_level = 1

    # 私有属性
    _enabled = False
    _notify = False
    _cron = None
    _onlyonce = False

    subscribe_oper: SubscribeOper = None

    def init_plugin(self, config: dict = None):
        self.subscribe_oper = SubscribeOper()
        if config:
            self._enabled = config.get("enabled", False)
            self._notify = config.get("notify", False)
            self._cron = config.get("cron")
            self._onlyonce = config.get("onlyonce", False)
        if self._onlyonce:
            logger.info(f"【{self.plugin_name}】：配置了“立即运行一次”，任务即将开始...")
            self.run_check()
            self._onlyonce = False
            self.update_config(self.get_config_dict())

    def get_state(self) -> bool:
        return self._enabled

    def get_service(self) -> List[Dict[str, Any]]:
        if not self.get_state(): return []
        if self._cron:
            return [{"id": f"{self.__class__.__name__}_check", "name": "已完成订阅检查", "trigger": CronTrigger.from_crontab(self._cron), "func": self.run_check, "kwargs": {}}]
        else:
            # 兼容性修正：移除 TimerUtils 的使用，直接返回一个默认的CRON任务
            return [{"id": f"{self.__class__.__name__}_check_random", "name": "已完成订阅检查 (默认)", "trigger": "cron", "func": self.run_check, "kwargs": {"hour": 3, "minute": "*/30"}}]

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []
        
    def get_api(self) -> List[Dict[str, Any]]:
        return []

    def get_page(self) -> List[dict]:
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {'component': 'VForm', 'content': [
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VSwitch', 'props': {'model': 'enabled', 'label': '启用插件', 'hint': '开启或关闭插件功能', 'persistent-hint': True}}]},
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VSwitch', 'props': {'model': 'notify', 'label': '发送通知', 'hint': '任务执行后发送通知消息', 'persistent-hint': True}}]}
                ]},
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 12}, 'content': [{'component': 'VCronField', 'props': {'model': 'cron', 'label': '执行周期 (CRON)', 'hint': '留空则每日凌晨3点后每30分钟随机执行', 'persistent-hint': True}}]}
                ]},
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VSwitch', 'props': {'model': 'onlyonce', 'label': '保存后立即运行一次', 'hint': '该开关会在执行后自动关闭', 'persistent-hint': True}}]}
                ]},
                {'component': 'VRow', 'content': [
                     {'component': 'VCol', 'props': {'cols': 12}, 'content': [
                         {'component': 'VAlert', 'props': {'type': 'info', 'variant': 'tonal', 'text': '此插件用于扫描所有已完成下载的订阅，并在日志中输出一个清晰的列表，显示每个媒体是由哪个用户订阅的。'}}
                     ]}
                ]}
            ]}
        ], self.get_config_dict()

    def stop_service(self):
        pass

    def run_check(self):
        logger.info(f"开始执行【{self.plugin_name}】任务...")
        try:
            all_subscriptions = self.subscribe_oper.list()
            if not all_subscriptions:
                logger.info(f"【{self.plugin_name}】：数据库中没有任何订阅记录。")
                return
            
            # 致命修正：使用正确的属性 `sub.state` 替换错误的 `sub.status`
            completed_subs = [sub for sub in all_subscriptions if sub.state == 'Downloaded']

            if not completed_subs:
                logger.info(f"【{self.plugin_name}】：未找到状态为“已完成”的订阅。")
                return

            logger.info(f"【{self.plugin_name}】：成功找到 {len(completed_subs)} 个已完成的订阅，正在整理输出...")
            output_lines = ["", f"--- [ {self.plugin_name} - 扫描结果 ] ---"]
            for sub in completed_subs:
                title = sub.name or "未知标题"
                user_name = sub.username or "未知用户"
                output_lines.append(f"  - 媒体: {title}")
                output_lines.append(f"  - 用户: {user_name}")
                output_lines.append("  ---------------------------------")
            
            result_text = "\n".join(output_lines)
            logger.info(result_text)

            if self._notify:
                self.post_message(mtype=NotificationType.Plugin, title=f"【{self.plugin_name}】执行完成", text=f"扫描完成，共发现 {len(completed_subs)} 个已完成的订阅。详情请查看插件日志。")
            
            logger.info(f"【{self.plugin_name}】任务执行完毕。")
        except Exception as e:
            logger.error(f"执行【{self.plugin_name}】插件时发生未知错误: {e}", exc_info=True)

    def get_config_dict(self):
        return { "enabled": False, "notify": False, "cron": "", "onlyonce": False }