# -*- coding: utf-8 -*-

"""
*************************************************
***      订阅历史清理工具 (CompletedSubscriptions)     ***
*************************************************
- 功能：查询订阅 history，并根据设定条件过滤、输出，或删除关联的媒体文件和历史记录。
- 作者：Gemini & 用户
- 规范：严格参照系统数据模型和范例插件结构编写。
"""

# 基础库导入
import time
from typing import Any, Dict, List, Tuple
from datetime import datetime, timedelta

# 第三方库导入
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

# MoviePilot 核心模块导入
from app.log import logger
from app.plugins import _PluginBase
from app.db.models.subscribehistory import SubscribeHistory
from app.db.models.transferhistory import TransferHistory
from app.db import db_query
from app.db.downloadhistory_oper import DownloadHistoryOper
from app.db.transferhistory_oper import TransferHistoryOper
from app.chain.storage import StorageChain
from app.core.event import eventmanager
from app.schemas import NotificationType, FileItem
from app.schemas.types import EventType


class CompletedSubscriptions(_PluginBase):
    """
    插件的主类，继承自 _PluginBase。
    实现了查询、过滤和删除订阅历史的核心功能。
    """
    # 插件元信息，用于在插件市场和系统内部展示
    plugin_name = "订阅历史清理工具"
    plugin_desc = "查询订阅 history，并根据设定条件过滤、输出，或删除关联的媒体文件和历史记录。"
    plugin_icon = "https://raw.githubusercontent.com/InfinityPacer/MoviePilot-Plugins/main/icons/subscribeassistant.png"
    plugin_version = "5.7.10" # 修复批量删除时 history 对象批量过期的问题
    plugin_author = "Gemini & 用户"
    author_url = "https://github.com/InfinityPacer/MoviePilot-Plugins"
    plugin_config_prefix = "sub_history_cleaner_"
    auth_level = 1

    # 定义插件的私有属性，用于存储从配置文件中加载的状态
    _enabled: bool = False
    _notify: bool = False
    _cron: str = None
    _onlyonce: bool = False
    _days_limit: int = None
    _users_list_str: str = "" # 用于在UI上显示和保存用户输入的原始字符串
    _users_config: Dict[str, int] = {} # 用于存储解析后的 "用户名: 天数" 映射关系
    _confirm_delete: bool = False
    _transfer_cleanup: bool = False

    # 定义需要用到的数据库操作类实例，在 init_plugin 中进行初始化
    download_history_oper: DownloadHistoryOper = None
    transfer_history_oper: TransferHistoryOper = None
    storage_chain: StorageChain = None

    @staticmethod
    def __format_season(season):
        """
        SubscribeHistory.season 通常是数字，而 DownloadHistory.seasons 使用 S01 格式。
        查询关联下载/整理记录前需要统一格式，否则电视剧到期记录会匹配不到历史。
        """
        if season is None or season == "":
            return None
        season_str = str(season).strip()
        if not season_str:
            return None
        if season_str.upper().startswith("S"):
            return season_str.upper()
        return f"S{int(season_str):02d}" if season_str.isdigit() else season_str

    @staticmethod
    def __parse_completed_time(date_value):
        """
        兼容不同版本或数据库中可能出现的完成时间格式。
        """
        if isinstance(date_value, datetime):
            return date_value
        if not date_value:
            return None
        date_str = str(date_value).strip()
        for date_format in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(date_str, date_format)
            except ValueError:
                continue
        return None

    @staticmethod
    def __append_unique(history_list, history_ids, history_item):
        if not history_item or not getattr(history_item, "id", None):
            return
        if history_item.id in history_ids:
            return
        history_ids.add(history_item.id)
        history_list.append(history_item)

    @staticmethod
    def __get_item_value(item, field, default=None):
        if isinstance(item, dict):
            return item.get(field, default)
        return getattr(item, field, default)

    @staticmethod
    def __get_transfer_fileitems(transfer):
        """
        整理记录里可能同时有源文件、目标文件，以及多文件转移时的 files 清单。
        都收集出来，避免只删到其中一集或一个链接文件。
        """
        fileitems = []
        for fileitem in (transfer.dest_fileitem, transfer.src_fileitem):
            if isinstance(fileitem, dict) and fileitem.get("path"):
                fileitems.append(fileitem)

        if isinstance(transfer.files, list):
            for fileitem in transfer.files:
                if isinstance(fileitem, dict) and fileitem.get("path"):
                    fileitems.append(fileitem)

        return fileitems

    def __snapshot_transfer(self, transfer):
        return {
            "id": transfer.id,
            "title": transfer.title,
            "src": transfer.src,
            "src_fileitem": transfer.src_fileitem,
            "fileitems": self.__get_transfer_fileitems(transfer)
        }

    @staticmethod
    def __snapshot_download(download):
        return {
            "id": download.id,
            "title": download.title
        }

    def __get_related_downloads(self, item):
        """
        根据订阅历史查找关联下载记录，并为缺少 tmdbid 的记录提供兜底查询。
        """
        season = self.__format_season(item.season) if item.type == "tv" else None
        downloads = []

        if item.tmdbid:
            downloads = self.download_history_oper.get_last_by(
                mtype=item.type,
                tmdbid=item.tmdbid,
                season=season
            )

        if not downloads and item.name and item.year:
            downloads = self.download_history_oper.get_last_by(
                mtype=item.type,
                title=item.name,
                year=item.year,
                season=season
            )

        if not downloads and (item.tmdbid or item.doubanid):
            downloads = self.download_history_oper.get_by_mediaid(
                tmdbid=item.tmdbid,
                doubanid=item.doubanid
            )
            if item.type == "tv" and season:
                downloads = [
                    download for download in downloads
                    if getattr(download, "seasons", None) == season
                ]
            elif item.type:
                downloads = [
                    download for download in downloads
                    if getattr(download, "type", None) == item.type
                ]

        return downloads or []

    def __get_related_transfers(self, item, downloads=None):
        """
        查找关联整理记录。优先使用下载 hash，同时按媒体信息补查，覆盖历史记录 hash 缺失或不一致的情况。
        """
        season = self.__format_season(item.season) if item.type == "tv" else None
        transfers = []
        transfer_ids = set()

        for download in downloads or []:
            download_hash = getattr(download, "download_hash", None)
            if not download_hash:
                continue
            for transfer in self.transfer_history_oper.list_by_hash(download_hash=download_hash) or []:
                self.__append_unique(transfers, transfer_ids, transfer)

        if item.tmdbid:
            for transfer in self.transfer_history_oper.get_by(
                mtype=item.type,
                tmdbid=item.tmdbid,
                season=season
            ) or []:
                self.__append_unique(transfers, transfer_ids, transfer)

        if item.name and item.year:
            for transfer in self.transfer_history_oper.get_by(
                mtype=item.type,
                title=item.name,
                year=item.year,
                season=season
            ) or []:
                self.__append_unique(transfers, transfer_ids, transfer)

        return transfers

    def __get_transfer_cleanup_results(self, db: Session, existing_transfer_ids):
        """
        通过插件自己的删除历史反查整理记录，用于清理旧版本没删干净的遗留文件。
        只处理删除历史里出现过的标题，并限制为删除时间之前的整理记录，避免误伤新下载的同名媒体。
        """
        cleanup_results = []
        deletion_history = self.get_data('deletion_history') or []
        if not deletion_history:
            return cleanup_results

        for deleted_item in deletion_history:
            title = deleted_item.get("title")
            if not title or title == "未知标题":
                continue

            query = db.query(TransferHistory).filter(TransferHistory.title == title)
            delete_time = self.__parse_completed_time(deleted_item.get("delete_time"))
            if delete_time:
                query = query.filter(TransferHistory.date <= deleted_item.get("delete_time"))

            transfers = []
            for transfer in query.all():
                if transfer.id in existing_transfer_ids:
                    continue
                if not self.__get_transfer_fileitems(transfer):
                    continue
                existing_transfer_ids.add(transfer.id)
                transfers.append(transfer)

            if not transfers:
                continue

            associated_files = []
            for transfer in transfers:
                for fileitem in self.__get_transfer_fileitems(transfer):
                    associated_files.append(fileitem.get("path"))

            cleanup_results.append({
                "history_item": {
                    "name": title,
                    "username": deleted_item.get("user", "删除历史"),
                    "date": f"整理记录遗留清理：{deleted_item.get('delete_time', '未知时间')}",
                    "poster": deleted_item.get("image"),
                    "backdrop": None
                },
                "downloads": [],
                "transfers": transfers,
                "files": associated_files,
                "transfer_cleanup": True
            })

        return cleanup_results

    def init_plugin(self, config: dict = None):
        """
        插件初始化方法。在 MoviePilot 启动或插件配置更新时被调用。
        :param config: 从数据库加载的插件配置字典。
        """
        # 实例化所有需要用到的操作类
        self.download_history_oper = DownloadHistoryOper()
        self.transfer_history_oper = TransferHistoryOper()
        self.storage_chain = StorageChain()

        # 如果存在配置，则从字典中加载各项配置
        if config:
            self._enabled = config.get("enabled", False)
            self._notify = config.get("notify", False)
            self._cron = config.get("cron")
            self._onlyonce = config.get("onlyonce", False)
            
            # 加载全局天数限制，并确保是整数
            days_str = config.get("days_limit")
            self._days_limit = int(days_str) if days_str and str(days_str).isdigit() else None
            
            # 加载用户列表原始字符串
            self._users_list_str = config.get("users_list", "")
            
            # 解析用户列表字符串为 "用户名: 天数" 的字典
            self._users_config = {}
            for line in self._users_list_str.split('\n'):
                # 忽略空行
                if not line.strip(): continue
                # 按冒号分割
                parts = [p.strip() for p in line.split(':')]
                username = parts[0]
                # 如果设置了独立天数且为数字，则使用；否则使用None，代表将使用全局天数
                days = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
                self._users_config[username] = days

            self._confirm_delete = config.get("confirm_delete", False)
            self._transfer_cleanup = config.get("transfer_cleanup", False)
        
        # 将加载后的配置（或默认配置）保存回数据库，确保配置持久化
        self.__update_config()

        # 如果用户在UI上开启了“立即运行一次”开关
        if self._onlyonce:
            logger.info(f"【{self.plugin_name}】：配置了“立即运行一次”，任务即将开始...")
            self.run_check()
            # 执行后立即关闭开关，并保存配置，防止重复执行
            self._onlyonce = False
            self.__update_config()

    def get_state(self) -> bool:
        """
        返回插件的启用状态。MoviePilot会根据此状态决定是否注册插件的服务。
        """
        return self._enabled

    def get_service(self) -> List[Dict[str, Any]]:
        """
        向 MoviePilot 系统注册后台定时服务。
        """
        # 如果插件未启用，则不注册任何服务
        if not self.get_state(): 
            return []
        
        # 如果用户自定义了 CRON 表达式，则使用它
        if self._cron:
            return [{"id": f"{self.__class__.__name__}_check", "name": "订阅历史清理", "trigger": CronTrigger.from_crontab(self._cron), "func": self.run_check, "kwargs": {}}]
        # 否则，使用默认的固定时间
        else:
            return [{"id": f"{self.__class__.__name__}_check_default", "name": "订阅历史清理 (默认)", "trigger": "cron", "func": self.run_check, "kwargs": {"hour": 3, "minute": 36}}]

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """
        注册远程命令，本插件未使用。
        """
        return []
        
    def get_api(self) -> List[Dict[str, Any]]:
        """
        对外暴露 API 接口，本插件未使用。
        """
        return []

    def get_page(self) -> List[dict]:
        """
        实现插件的详情页面，用于展示已删除的历史记录。
        """
        # 从插件的持久化数据中读取已保存的删除 history
        deletion_history = self.get_data('deletion_history')
        if not deletion_history:
            # 如果没有 history 记录，显示提示信息
            return [
                {'component': 'div', 'text': '暂无删除记录', 'props': {'class': 'text-center text-h6 pa-4'}}
            ]
        
        # 将 history 记录按删除时间降序排序，最新的显示在最前面
        deletion_history = sorted(deletion_history, key=lambda x: x.get('delete_time'), reverse=True)
        
        # 使用展开面板分批展示，避免依赖页面内部临时状态导致分页按钮无效
        items_per_page = 200
        pages = [deletion_history[i:i + items_per_page] for i in range(0, len(deletion_history), items_per_page)]
        panel_items = []
        for i, page_items in enumerate(pages):
            start = i * items_per_page + 1
            end = start + len(page_items) - 1
            cards = []
            for item in page_items:
                cards.append({
                    'component': 'VCard', 'content': [
                        {'component': 'div', 'props': {'class': 'd-flex flex-no-wrap justify-space-between'}, 'content': [
                            {'component': 'div', 'content': [
                                {'component': 'VCardTitle', 'text': item.get("title", "未知标题")},
                                {'component': 'VCardSubtitle', 'text': f"用户: {item.get('user', '未知')}"},
                                {'component': 'VCardText', 'text': f"删除时间: {item.get('delete_time', '未知')}"}
                            ]},
                            {'component': 'VAvatar', 'props': {'class': 'ma-3', 'size': '80', 'rounded': 'lg'}, 'content': [
                                {'component': 'VImg', 'props': {'src': item.get('image', ''), 'cover': True}}
                            ]}
                        ]}
                    ]
                })

            panel_items.append({
                'component': 'VExpansionPanel',
                'content': [
                    {'component': 'VExpansionPanelTitle', 'text': f"第 {i + 1} 页（{start}-{end} / 共 {len(deletion_history)} 条）"},
                    {'component': 'VExpansionPanelText', 'content': [
                        {
                            'component': 'div',
                            'props': {'class': 'grid gap-3 grid-info-card'},
                            'content': cards
                        }
                    ]}
                ]
            })

        return [{
            'component': 'VExpansionPanels',
            'props': {'variant': 'accordion', 'class': 'mt-4'},
            'content': panel_items
        }]


    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        定义并返回插件在前端的配置界面。
        返回一个元组，第一部分是UI结构定义，第二部分是各项的默认值。
        """
        return [
            {'component': 'VForm', 'content': [
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VSwitch', 'props': {'model': 'enabled', 'label': '启用插件', 'hint': '开启或关闭插件功能', 'persistent-hint': True}}]},
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VSwitch', 'props': {'model': 'notify', 'label': '发送通知', 'hint': '任务执行后发送通知消息', 'persistent-hint': True}}]}
                ]},
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VTextField', 'props': {'model': 'days_limit', 'label': '全局天数限制', 'type': 'number', 'hint': '只处理超过指定天数的记录，留空则不执行', 'persistent-hint': True}}]},
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VCronField', 'props': {'model': 'cron', 'label': '执行周期 (CRON)', 'hint': '留空则每日凌晨3点36分执行一次', 'persistent-hint': True}}]}
                ]},
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12}, 'content': [{'component': 'VTextarea', 'props': {'model': 'users_list', 'label': '用户列表', 'rows': 4, 'hint': '每行一个用户，支持格式 "用户名" 或 "用户名:天数" 来覆盖全局天数限制。', 'persistent-hint': True}}]}
                ]},
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 4}, 'content': [{'component': 'VSwitch', 'props': {'model': 'onlyonce', 'label': '保存后立即运行一次', 'hint': '该开关会在执行后自动关闭', 'persistent-hint': True}}]},
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 4}, 'content': [{'component': 'VSwitch', 'props': {'model': 'confirm_delete', 'label': '确认删除', 'color': 'error', 'hint': '开启后将真实删除文件和订阅 history，请谨慎操作！', 'persistent-hint': True}}]},
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 4}, 'content': [{'component': 'VSwitch', 'props': {'model': 'transfer_cleanup', 'label': '整理记录遗留清理', 'color': 'warning', 'hint': '按插件删除历史反查整理记录，用于清理旧版本未删干净的文件，建议临时开启一次。', 'persistent-hint': True}}]}
                ]},
                {'component': 'VRow', 'content': [
                     {'component': 'VCol', 'props': {'cols': 12}, 'content': [
                         {'component': 'VAlert', 'props': {'type': 'info', 'variant': 'tonal', 'text': '此插件会扫描所有订阅 history。只有当“全局天数限制”或用户独立天数，以及“用户列表”都填写时，才会按订阅 history 执行。开启“整理记录遗留清理”后，会额外根据本插件已删除历史反查整理记录，用于清理旧版本未删干净的文件。'}}
                     ]}
                ]}
            ]}
        ], self.get_config_dict()

    def stop_service(self):
        """
        插件停止时调用的方法，本插件无需特殊清理。
        """
        pass

    @db_query
    def _execute_db_operations(self, db: Session = None):
        """
        一个专门用于执行所有数据库操作的私有方法，以确保 db 会话被正确注入和使用。
        它会返回一个包含完整信息的列表，用于后续的日志输出或删除记录。
        """
        logger.info("进入 _execute_db_operations 方法...")
        try:
            # 1. 查询所有 history 记录
            all_history = db.query(SubscribeHistory).order_by(SubscribeHistory.date.desc()).all()
            
            # 2. 筛选满足天数和用户条件的记录
            filtered_history = []
            current_time = datetime.now()
            for item in all_history:
                # 检查用户名是否在配置的列表中
                if item.username in self._users_config:
                    # 获取该用户的独立天数限制，如果不存在，则使用全局天数限制
                    user_days_limit = self._users_config.get(item.username) or self._days_limit
                    
                    # 如果最终的天数限制不为空，则进行时间判断
                    if user_days_limit is not None:
                        try:
                            completed_time = self.__parse_completed_time(item.date)
                            if not completed_time:
                                logger.warning(f"无法解析记录 '{item.name}' 的完成时间: {item.date}，跳过该条记录。")
                                continue
                            if (current_time - completed_time) > timedelta(days=user_days_limit):
                                filtered_history.append(item)
                        except (ValueError, TypeError):
                            logger.warning(f"无法解析记录 '{item.name}' 的完成时间: {item.date}，跳过该条记录。")
                            continue

            # 3. 为每一条满足条件的记录，查找其关联的文件
            results = []
            existing_transfer_ids = set()
            for item in filtered_history:
                associated_files = []
                downloads = self.__get_related_downloads(item)
                transfers = self.__get_related_transfers(item, downloads)
                for transfer in transfers:
                    existing_transfer_ids.add(transfer.id)
                    for fileitem in self.__get_transfer_fileitems(transfer):
                        associated_files.append(fileitem.get('path'))
                results.append({
                    "history_item": item,
                    "downloads": downloads,
                    "transfers": transfers,
                    "files": associated_files
                })

            if self._transfer_cleanup:
                results.extend(self.__get_transfer_cleanup_results(db, existing_transfer_ids))

            if not results:
                return []

            # 4. 如果是删除模式，则执行删除
            if self._confirm_delete:
                deleted_items_for_page = []
                for result in results:
                    item = result["history_item"]
                    downloads = [
                        self.__snapshot_download(download)
                        for download in result.get("downloads") or []
                    ]
                    transfers = [
                        self.__snapshot_transfer(transfer)
                        for transfer in result.get("transfers") or []
                    ]
                    deleted_file_paths = set()
                    
                    for transfer in transfers:
                        transfer_id = transfer["id"]
                        transfer_title = transfer["title"]
                        transfer_src = transfer["src"]
                        transfer_src_fileitem = transfer["src_fileitem"]
                        transfer_fileitems = transfer["fileitems"]
                        for fileitem in transfer_fileitems:
                            file_path = fileitem.get("path")
                            if not file_path or file_path in deleted_file_paths:
                                continue
                            deleted_file_paths.add(file_path)
                            try:
                                self.storage_chain.delete_file(FileItem(**fileitem))
                                if transfer_src_fileitem and file_path == transfer_src_fileitem.get("path"):
                                    eventmanager.send_event(EventType.DownloadFileDeleted, {"src": transfer_src})
                            except Exception as err:
                                logger.warning(f"删除文件失败: {file_path}，错误: {err}")
                        self.transfer_history_oper.delete(transfer_id)
                        logger.info(f"已删除整理 history 记录: {transfer_title} (ID: {transfer_id})")

                    for download in downloads:
                        download_id = download["id"]
                        download_title = download["title"]
                        self.download_history_oper.delete_history(download_id)
                        logger.info(f"已删除下载 history 记录: {download_title} (ID: {download_id})")

                    item_name = self.__get_item_value(item, "name", "未知标题")
                    item_user = self.__get_item_value(item, "username", "未知用户")
                    item_image = self.__get_item_value(item, "poster") or self.__get_item_value(item, "backdrop")

                    if not result.get("transfer_cleanup"):
                        item_id = item.id
                        # 删除订阅 history 记录
                        SubscribeHistory.delete(db, item_id)
                        logger.info(f"已删除订阅 history 记录: {item_name} (ID: {item_id})")

                        # 将被删除的条目信息添加到列表中，用于更新详情页
                        deleted_items_for_page.append({
                            "title": item_name,
                            "user": item_user,
                            "type": self.__get_item_value(item, "type"),
                            "year": self.__get_item_value(item, "year"),
                            "season": self.__get_item_value(item, "season"),
                            "tmdbid": self.__get_item_value(item, "tmdbid"),
                            "doubanid": self.__get_item_value(item, "doubanid"),
                            "image": item_image,
                            "delete_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                        })
                    else:
                        logger.info(f"已按整理记录清理遗留文件: {item_name}")
                
                # 更新详情页的删除 history
                if deleted_items_for_page:
                    all_deleted_history = self.get_data('deletion_history') or []
                    all_deleted_history.extend(deleted_items_for_page)
                    # 仅保留最近的1000条记录，防止数据文件过大
                    self.save_data('deletion_history', all_deleted_history[-1000:])

            return results

        except Exception as e:
            logger.error(f"【{self.plugin_name}】：在数据库操作中发生错误: {str(e)}", exc_info=True)
            return []

    def run_check(self):
        """
        插件的核心执行逻辑。
        """
        logger.info(f"开始执行【{self.plugin_name}】任务...")
        
        # 检查前置条件：全局天数限制和用户列表是否都有效
        if not self._transfer_cleanup and self._days_limit is None and not any(self._users_config.values()):
            logger.info(f"【{self.plugin_name}】：全局天数未设置，且没有任何用户设置独立天数，任务中止。")
            return
        if not self._transfer_cleanup and not self._users_config:
             logger.info(f"【{self.plugin_name}】：用户列表未填写，任务中止。")
             return
            
        logger.info(f"【{self.plugin_name}】：全局天数限制为 {self._days_limit} 天，用户配置为 {self._users_config}，整理记录遗留清理为 {self._transfer_cleanup}")
        if self._confirm_delete:
            logger.warning(f"【{self.plugin_name}】：已开启“确认删除”模式，将会真实删除文件和 history 记录！")
        else:
            logger.info(f"【{self.plugin_name}】：当前为预览模式，仅输出符合条件的媒体及其关联文件。")

        try:
            # 将所有数据库操作集中到一个带有 @db_query 的方法中执行
            processed_results = self._execute_db_operations()

            # 根据操作结果，生成总结信息和日志
            if not processed_results:
                summary_text = "扫描完成，没有找到任何满足条件的记录。"
            else:
                output_lines = ["", f"--- [ {self.plugin_name} - {'删除' if self._confirm_delete else '预览'}结果 ] ---"]
                
                for result in processed_results:
                    item = result["history_item"]
                    files = result["files"]
                    output_lines.append(f"  - 媒体: {self.__get_item_value(item, 'name', '未知标题')}")
                    output_lines.append(f"  - 用户: {self.__get_item_value(item, 'username', '未知用户')}")
                    output_lines.append(f"  - 完成时间: {self.__get_item_value(item, 'date', '未知时间')}")
                    if result.get("transfer_cleanup"):
                        output_lines.append("  - 类型: 整理记录遗留清理")
                    
                    if files:
                        output_lines.append("  - 关联文件:")
                        for file_path in files:
                            output_lines.append(f"    - {file_path}")
                    else:
                        output_lines.append("  - 关联文件: 未找到关联的下载或整理记录。")
                    
                    output_lines.append("  ---------------------------------")
                
                logger.info("\n".join(output_lines))

                if self._confirm_delete:
                    summary_text = f"扫描完成，共处理了 {len(processed_results)} 条订阅 history / 整理记录遗留项及其关联文件。"
                else:
                    summary_text = f"预览完成，共找到 {len(processed_results)} 条满足条件的记录。"
            
            logger.info(f"【{self.plugin_name}】任务执行完毕。{summary_text}")
            if self._notify:
                self.post_message(mtype=NotificationType.Plugin, title=f"【{self.plugin_name}】执行完成", text=summary_text)

        except Exception as e:
            logger.error(f"执行【{self.plugin_name}】插件时发生未知错误: {e}", exc_info=True)

    def get_config_dict(self):
        """
        将当前插件的所有配置项打包成一个字典，用于保存。
        """
        return { 
            "enabled": self._enabled, 
            "notify": self._notify, 
            "cron": self._cron, 
            "onlyonce": self._onlyonce, 
            "days_limit": self._days_limit, 
            "users_list": self._users_list_str, # 保存原始字符串，以便UI正确显示
            "confirm_delete": self._confirm_delete,
            "transfer_cleanup": self._transfer_cleanup
        }
    
    def __update_config(self):
        """
        一个私有辅助方法，用于将当前配置保存回数据库。
        """
        self.update_config(self.get_config_dict())
