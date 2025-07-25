# -*- coding: utf-8 -*-

"""
*************************************************
***      订阅历史查看器 (CompletedSubscriptions)     ***
*************************************************
- 功能：查询订阅历史记录，并根据设定的天数和用户名进行过滤后输出。
- 作者：Gemini & 用户
- 规范：严格参照系统数据模型和范例插件结构编写。
"""

from typing import Any, Dict, List, Tuple
from datetime import datetime, timedelta
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
    plugin_desc = "查询订阅历史记录，并根据设定的天数和用户名进行过滤后输出。"
    plugin_icon = "https://raw.githubusercontent.com/InfinityPacer/MoviePilot-Plugins/main/icons/subscribeassistant.png"
    plugin_version = "4.0.0" # 新增功能版
    plugin_author = "Gemini & 用户"
    author_url = "https://github.com/InfinityPacer/MoviePilot-Plugins"
    plugin_config_prefix = "sub_history_viewer_"
    auth_level = 1

    # 私有属性
    _enabled = False
    _notify = False
    _cron = None
    _onlyonce = False
    _days_limit = None # 新增：天数限制
    _users_list = []   # 新增：用户列表

    def init_plugin(self, config: dict = None):
        if config:
            self._enabled = config.get("enabled", False)
            self._notify = config.get("notify", False)
            self._cron = config.get("cron")
            self._onlyonce = config.get("onlyonce", False)
            
            # 新增：加载天数和用户列表配置
            days_str = config.get("days_limit")
            self._days_limit = int(days_str) if days_str else None
            
            users_str = config.get("users_list", "")
            self._users_list = [user.strip() for user in users_str.split('\n') if user.strip()]

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
                # 新增：天数和用户名字段
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VTextField', 'props': {'model': 'days_limit', 'label': '天数限制', 'type': 'number', 'hint': '只输出超过指定天数的记录，留空则不执行', 'persistent-hint': True}}]},
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VCronField', 'props': {'model': 'cron', 'label': '执行周期 (CRON)', 'hint': '留空则每日凌晨3点后每30分钟随机执行', 'persistent-hint': True}}]}
                ]},
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12}, 'content': [{'component': 'VTextarea', 'props': {'model': 'users_list', 'label': '用户列表', 'rows': 4, 'hint': '每行一个用户名，只输出这些用户的记录，留空则不执行', 'persistent-hint': True}}]}
                ]},
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VSwitch', 'props': {'model': 'onlyonce', 'label': '保存后立即运行一次', 'hint': '该开关会在执行后自动关闭', 'persistent-hint': True}}]}
                ]},
                {'component': 'VRow', 'content': [
                     {'component': 'VCol', 'props': {'cols': 12}, 'content': [
                         {'component': 'VAlert', 'props': {'type': 'info', 'variant': 'tonal', 'text': '此插件会扫描所有订阅历史。只有当“天数限制”和“用户列表”都填写时，才会根据条件过滤并输出结果。'}}
                     ]}
                ]}
            ]}
        ], self.get_config_dict()

    def stop_service(self):
        pass

    @db_query
    def get_all_subscribe_history(self, db: Session = None) -> List[SubscribeHistory]:
        """
        获取所有的订阅历史记录
        """
        logger.info("进入 get_all_subscribe_history 方法...")
        try:
            # 移除 .limit() 限制，获取所有记录
            all_history = db.query(SubscribeHistory).order_by(SubscribeHistory.date.desc()).all()
            logger.info(f"查询完成，共获取到 {len(all_history)} 条总历史记录。")
            return all_history
        except Exception as e:
            logger.error(f"【{self.plugin_name}】：在 get_all_subscribe_history 方法中获取订阅历史失败: {str(e)}", exc_info=True)
            return []

    def run_check(self):
        """
        插件的核心执行逻辑。
        """
        logger.info(f"开始执行【{self.plugin_name}】任务...")
        
        # 新增：检查前置条件
        if self._days_limit is None or not self._users_list:
            logger.info(f"【{self.plugin_name}】：天数限制或用户列表未填写，任务中止。")
            return
            
        logger.info(f"【{self.plugin_name}】：天数限制为 {self._days_limit} 天，用户列表为 {self._users_list}")

        try:
            all_history = self.get_all_subscribe_history()

            if not all_history:
                logger.info(f"【{self.plugin_name}】：订阅历史记录为空。")
                return

            # 新增：根据天数和用户名进行过滤
            filtered_history = []
            current_time = datetime.now()
            for item in all_history:
                # 检查用户名是否匹配
                if item.username in self._users_list:
                    # 检查时间是否超过天数
                    try:
                        # MoviePilot 存储的时间格式通常是 'YYYY-MM-DD HH:MM:SS'
                        completed_time = datetime.strptime(item.date, '%Y-%m-%d %H:%M:%S')
                        if (current_time - completed_time) > timedelta(days=self._days_limit):
                            filtered_history.append(item)
                    except (ValueError, TypeError):
                        logger.warning(f"无法解析记录 '{item.name}' 的完成时间: {item.date}，跳过该条记录。")
                        continue

            if not filtered_history:
                logger.info(f"【{self.plugin_name}】：没有找到满足条件（超过 {self._days_limit} 天且用户在指定列表中）的订阅历史记录。")
                return

            logger.info(f"【{self.plugin_name}】：成功筛选出 {len(filtered_history)} 条满足条件的记录，正在整理输出...")
            output_lines = ["", f"--- [ {self.plugin_name} - 扫描结果 ] ---"]
            for item in filtered_history:
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
                self.post_message(mtype=NotificationType.Plugin, title=f"【{self.plugin_name}】执行完成", text=f"扫描完成，共找到 {len(filtered_history)} 条满足条件的订阅历史。详情请查看插件日志。")
            
            logger.info(f"【{self.plugin_name}】任务执行完毕。")
        except Exception as e:
            logger.error(f"执行【{self.plugin_name}】插件时发生未知错误: {e}", exc_info=True)

    def get_config_dict(self):
        # 新增：返回新的配置项的默认值
        return { 
            "enabled": False, 
            "notify": False, 
            "cron": "", 
            "onlyonce": False, 
            "days_limit": None, 
            "users_list": "" 
        }