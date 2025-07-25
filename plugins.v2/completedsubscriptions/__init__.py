# -*- coding: utf-8 -*-

"""
*************************************************
***      订阅历史查看器 (CompletedSubscriptions)     ***
*************************************************
- 功能：查询订阅历史记录，并清晰地展示已完成订阅的媒体以及对应的用户。
- 作者：Gemini & 用户
- 规范：严格参照系统数据模型和范例插件结构编写。
"""

from typing import Any, Dict, List, Tuple
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

from app.log import logger
from app.plugins import _PluginBase
from app.db.models.subscribehistory import SubscribeHistory
from app.db import db_query
from app.schemas import NotificationType

class CompletedSubscriptions(_PluginBase):
    # 插件元信息
    plugin_name = "订阅历史查看器"
    plugin_desc = "查询订阅历史记录，并清晰地展示已完成订阅的媒体以及对应的用户。"
    plugin_icon = "https://raw.githubusercontent.com/InfinityPacer/MoviePilot-Plugins/main/icons/subscribeassistant.png"
    plugin_version = "3.4.0" # 最终版，修正了数据库查询逻辑
    plugin_author = "Gemini & 用户"
    author_url = "https://github.com/InfinityPacer/MoviePilot-Plugins"
    plugin_config_prefix = "sub_history_viewer_"
    auth_level = 1

    # 私有属性
    _enabled = False
    _notify = False
    _cron = None
    _onlyonce = False
    _display_limit = 50

    def init_plugin(self, config: dict = None):
        if config:
            self._enabled = config.get("enabled", False)
            self._notify = config.get("notify", False)
            self._cron = config.get("cron")
            self._onlyonce = config.get("onlyonce", False)
            self._display_limit = int(config.get("display_limit", 50))

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
            return [{"id": f"{self.__class__.__name__}_check", "name": "订阅历史检查", "trigger": CronTrigger.from_crontab(self._cron), "func": self.run_check, "kwargs": {}}]
        else:
            return [{"id": f"{self.__class__.__name__}_check_random", "name": "订阅历史检查 (默认)", "trigger": "cron", "func": self.run_check, "kwargs": {"hour": 3, "minute": "*/30"}}]

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
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VCronField', 'props': {'model': 'cron', 'label': '执行周期 (CRON)', 'hint': '留空则每日凌晨3点后每30分钟随机执行', 'persistent-hint': True}}]},
                     {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VTextField', 'props': {'model': 'display_limit', 'label': '显示数量', 'type': 'number', 'hint': '在日志中显示最近N条历史记录', 'persistent-hint': True}}]}
                ]},
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VSwitch', 'props': {'model': 'onlyonce', 'label': '保存后立即运行一次', 'hint': '该开关会在执行后自动关闭', 'persistent-hint': True}}]}
                ]},
                {'component': 'VRow', 'content': [
                     {'component': 'VCol', 'props': {'cols': 12}, 'content': [
                         {'component': 'VAlert', 'props': {'type': 'info', 'variant': 'tonal', 'text': '此插件用于扫描并展示订阅历史，即所有生命周期已结束的订阅任务。'}}
                     ]}
                ]}
            ]}
        ], self.get_config_dict()

    def stop_service(self):
        pass

    @db_query
    def get_subscribe_history(self, db: Session = None) -> List[SubscribeHistory]:
        """
        致命修正：不再对 `type` 字段进行任何过滤，直接查询所有历史记录。
        """
        logger.info("进入 get_subscribe_history 方法...")
        try:
            total_count = db.query(SubscribeHistory).count()
            logger.info(f"数据库中 SubscribeHistory 表的总记录数为: {total_count}")
            
            logger.info(f"开始查询所有类型的订阅历史，不进行 type 过滤，数量限制: {self._display_limit}")
            
            # 致命修正：移除按类型的分次查询，直接查询所有记录并排序
            all_history = db.query(SubscribeHistory).order_by(
                SubscribeHistory.date.desc()
            ).limit(self._display_limit).all()
            
            logger.info(f"查询完成，获取到 {len(all_history)} 条记录。")
            return all_history
        except Exception as e:
            logger.error(f"【{self.plugin_name}】：在 get_subscribe_history 方法中获取订阅历史失败: {str(e)}", exc_info=True)
            return []

    def run_check(self):
        """
        插件的核心执行逻辑。
        """
        logger.info(f"开始执行【{self.plugin_name}】任务...")
        try:
            all_history = self.get_subscribe_history()

            if not all_history:
                logger.info(f"【{self.plugin_name}】：订阅历史记录为空。")
                return

            logger.info(f"【{self.plugin_name}】：成功获取到 {len(all_history)} 条订阅历史记录，正在整理输出...")
            output_lines = ["", f"--- [ {self.plugin_name} - 扫描结果 ] ---"]
            for item in all_history:
                title = item.name or "未知标题"
                user_name = item.username or "未知用户"
                completed_time = item.date or "未知时间"
                
                output_lines.append(f"  - 媒体: {title}")
                output_lines.append(f"  - 用户: {user_name}")
                output_lines.append(f"  - 完成时间: {completed_time}")
                output_lines.append("  ---------------------------------")
            
            result_text = "\n".join(output_lines)
            logger.info(result_text)

            if self._notify:
                self.post_message(mtype=NotificationType.Plugin, title=f"【{self.plugin_name}】执行完成", text=f"扫描完成，共展示 {len(all_history)} 条最近完成的订阅历史。详情请查看插件日志。")
            
            logger.info(f"【{self.plugin_name}】任务执行完毕。")
        except Exception as e:
            logger.error(f"执行【{self.plugin_name}】插件时发生未知错误: {e}", exc_info=True)

    def get_config_dict(self):
        return { "enabled": False, "notify": False, "cron": "", "onlyonce": False, "display_limit": 50 }