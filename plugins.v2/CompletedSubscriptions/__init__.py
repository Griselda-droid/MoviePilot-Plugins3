# -*- coding: utf-8 -*-

"""
*************************************************
***      已完成订阅查看器 (CompletedSubscriptions)     ***
*************************************************
"""
from typing import Any, Dict, List, Tuple
from apscheduler.triggers.cron import CronTrigger

from app.log import logger
from app.plugins import _PluginBase
from app.helper.subscribe import SubscribeHelper
from app.helper.user import UserHelper
from app.schemas import NotificationType
from app.utils.timer import TimerUtils

class CompletedSubscriptions(_PluginBase):
    plugin_name = "已完成订阅查看器"
    plugin_desc = "定时获取所有已完成的订阅，并清晰地展示订阅的媒体以及对应的用户。"
    plugin_icon = "task-complete.png"
    plugin_version = "1.0.0"
    plugin_author = "Gemini & 用户"
    author_url = "https://github.com/InfinityPacer/MoviePilot-Plugins"
    plugin_config_prefix = "completed_subs_"
    auth_level = 1

    _enabled = False
    _notify = False
    _cron = None
    _onlyonce = False

    subscribe_helper: SubscribeHelper = None
    user_helper: UserHelper = None

    def init_plugin(self, config: dict = None):
        self.subscribe_helper = SubscribeHelper()
        self.user_helper = UserHelper()
        if config:
            self._enabled = config.get("enabled", False)
            self._notify = config.get("notify", False)
            self._cron = config.get("cron")
            self._onlyonce = config.get("onlyonce", False)
        if self._onlyonce:
            logger.info(f"{self.plugin_name}：配置了立即运行一次。")
            self.run_check()
            self._onlyonce = False
            self.__update_config()

    def get_state(self) -> bool:
        return self._enabled

    def get_service(self) -> List[Dict[str, Any]]:
        if not self.get_state():
            return []
        if self._cron:
            return [{"id": "CompletedSubscriptions_check", "name": "已完成订阅检查任务", "trigger": CronTrigger.from_crontab(self._cron), "func": self.run_check, "kwargs": {}}]
        else:
            random_trigger = TimerUtils.random_scheduler(num_executions=1, begin_hour=2, end_hour=5)[0]
            return [{"id": f"CompletedSubscriptions_check_random", "name": "已完成订阅检查任务 (随机)", "trigger": "cron", "func": self.run_check, "kwargs": {"hour": random_trigger.hour, "minute": random_trigger.minute}}]

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {'component': 'VForm', 'content': [
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VSwitch', 'props': {'model': 'enabled', 'label': '启用插件'}}]},
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VSwitch', 'props': {'model': 'notify', 'label': '发送通知'}}]}
                ]},
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 12}, 'content': [{'component': 'VTextField', 'props': {'model': 'cron', 'label': '执行周期 (CRON表达式)', 'placeholder': '留空则每日凌晨随机执行，例如: 0 3 * * *'}}]}
                ]},
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VSwitch', 'props': {'model': 'onlyonce', 'label': '保存后立即运行一次', 'description': '该开关会在执行后自动关闭'}}]}
                ]},
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12}, 'content': [{'component': 'VAlert', 'props': {'type': 'info', 'variant': 'tonal', 'text': '此插件用于扫描所有已完成下载的订阅，并在日志中输出一个清晰的列表，显示每个媒体是由哪个用户订阅的。'}}]}
                ]}
            ]}
        ], {"enabled": False, "notify": False, "cron": "", "onlyonce": False}

    def stop_service(self):
        self._enabled = False

    def run_check(self):
        logger.info(f"开始执行【{self.plugin_name}】任务...")
        try:
            all_subscriptions = self.subscribe_helper.get_all()
            if not all_subscriptions:
                logger.info("数据库中没有任何订阅记录。")
                return
            completed_subs = [sub for sub in all_subscriptions if sub.status == 'Downloaded']
            if not completed_subs:
                logger.info("未找到已完成的订阅。")
                return
            logger.info(f"成功找到 {len(completed_subs)} 个已完成的订阅，正在整理输出...")
            output_lines = ["", f"--- [ {self.plugin_name} - 扫描结果 ] ---"]
            for sub in completed_subs:
                title = sub.get_title()
                user_name = "未知用户"
                if sub.user_id:
                    user_info = self.user_helper.get(sub.user_id)
                    if user_info:
                        user_name = user_info.name
                output_lines.append(f"  - 媒体: {title}")
                output_lines.append(f"  - 用户: {user_name}")
                output_lines.append("  ---------------------------------")
            result_text = "\n".join(output_lines)
            logger.info(result_text)
            if self._notify:
                self.post_message(mtype=NotificationType.Plugin, title=f"{self.plugin_name} 执行完成", text=f"扫描完成，共发现 {len(completed_subs)} 个已完成的订阅。详情请查看插件日志。")
            logger.info(f"【{self.plugin_name}】任务执行完毕。")
        except Exception as e:
            logger.error(f"执行【{self.plugin_name}】插件时发生未知错误: {e}", exc_info=True)

    def __update_config(self):
        self.update_config({"enabled": self._enabled, "notify": self._notify, "cron": self._cron, "onlyonce": self._onlyonce})