from __future__ import annotations

import base64
import imaplib
import json
import mimetypes
import os
import re
import smtplib
import ssl
import threading
import time
import tkinter as tk
import uuid
from dataclasses import asdict, dataclass
from email import policy
from email.header import decode_header
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import formataddr, getaddresses, parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from clicker_tool import ClickerTheme, minimize_frameless_window

try:
    import win32cred

    WIN_CREDENTIALS_AVAILABLE = True
except Exception:  # pragma: no cover - optional on non-Windows systems
    win32cred = None
    WIN_CREDENTIALS_AVAILABLE = False


CREDENTIAL_PREFIX = "Passer.Mail"
MAX_MESSAGES = 100
PROVIDER_PRESETS = {
    "QQ 邮箱": {
        "imap_host": "imap.qq.com", "imap_port": 993, "imap_security": "SSL/TLS",
        "smtp_host": "smtp.qq.com", "smtp_port": 465, "smtp_security": "SSL/TLS",
    },
    "163 邮箱": {
        "imap_host": "imap.163.com", "imap_port": 993, "imap_security": "SSL/TLS",
        "smtp_host": "smtp.163.com", "smtp_port": 465, "smtp_security": "SSL/TLS",
    },
    "Gmail": {
        "imap_host": "imap.gmail.com", "imap_port": 993, "imap_security": "SSL/TLS",
        "smtp_host": "smtp.gmail.com", "smtp_port": 465, "smtp_security": "SSL/TLS",
    },
    "Outlook / Hotmail": {
        "imap_host": "outlook.office365.com", "imap_port": 993, "imap_security": "SSL/TLS",
        "smtp_host": "smtp.office365.com", "smtp_port": 587, "smtp_security": "STARTTLS",
    },
    "自定义": {},
}


def decode_header_text(value: str | None) -> str:
    if not value:
        return ""
    chunks: list[str] = []
    try:
        parts = decode_header(value)
    except Exception:
        return str(value)
    for chunk, encoding in parts:
        if isinstance(chunk, bytes):
            for codec in (encoding, "utf-8", "gb18030", "latin-1"):
                if not codec:
                    continue
                try:
                    chunks.append(chunk.decode(codec, errors="replace"))
                    break
                except (LookupError, UnicodeError):
                    continue
        else:
            chunks.append(str(chunk))
    return "".join(chunks).strip()


def decode_imap_utf7(value: str) -> str:
    result: list[str] = []
    index = 0
    while index < len(value):
        if value[index] != "&":
            result.append(value[index])
            index += 1
            continue
        end = value.find("-", index)
        if end < 0:
            result.append(value[index:])
            break
        token = value[index + 1:end]
        if not token:
            result.append("&")
        else:
            payload = token.replace(",", "/")
            payload += "=" * ((4 - len(payload) % 4) % 4)
            try:
                result.append(base64.b64decode(payload).decode("utf-16-be"))
            except Exception:
                result.append(value[index:end + 1])
        index = end + 1
    return "".join(result)


def parse_folder_line(raw: bytes | str) -> tuple[str, str, tuple[str, ...]] | None:
    text = raw.decode("ascii", errors="replace") if isinstance(raw, bytes) else str(raw)
    match = re.match(r"^\((?P<flags>[^)]*)\)\s+(?:\"[^\"]*\"|NIL)\s+(?P<name>.+)$", text.strip())
    if not match:
        return None
    name = match.group("name").strip()
    if name.startswith('"') and name.endswith('"'):
        name = name[1:-1].replace(r"\"", '"').replace(r"\\", "\\")
    flags = tuple(part.casefold() for part in match.group("flags").split())
    return name, decode_imap_utf7(name), flags


def quote_mailbox(name: str) -> str:
    return '"' + name.replace("\\", "\\\\").replace('"', r'\"') + '"'


def credential_target(account_id: str) -> str:
    return f"{CREDENTIAL_PREFIX}/{account_id}"


def save_credential(account_id: str, username: str, password: str) -> None:
    if not WIN_CREDENTIALS_AVAILABLE or not password:
        return
    win32cred.CredWrite(
        {
            "Type": win32cred.CRED_TYPE_GENERIC,
            "TargetName": credential_target(account_id),
            "UserName": username,
            "CredentialBlob": password,
            "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
        },
        0,
    )


def load_credential(account_id: str) -> str:
    if not WIN_CREDENTIALS_AVAILABLE:
        return ""
    try:
        value = win32cred.CredRead(credential_target(account_id), win32cred.CRED_TYPE_GENERIC, 0)
    except Exception:
        return ""
    blob = value.get("CredentialBlob", b"")
    if isinstance(blob, str):
        return blob
    if isinstance(blob, bytes):
        for encoding in ("utf-16-le", "utf-8"):
            try:
                return blob.decode(encoding).rstrip("\x00")
            except UnicodeError:
                continue
    return ""


def delete_credential(account_id: str) -> None:
    if not WIN_CREDENTIALS_AVAILABLE:
        return
    try:
        win32cred.CredDelete(credential_target(account_id), win32cred.CRED_TYPE_GENERIC, 0)
    except Exception:
        pass


class _HTMLTextExtractor(HTMLParser):
    BREAK_TAGS = {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, _attrs) -> None:
        if tag in {"script", "style"}:
            self.skip_depth += 1
        elif tag in self.BREAK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.skip_depth:
            self.skip_depth -= 1
        elif tag in self.BREAK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            self.parts.append(data)

    def text(self) -> str:
        value = "".join(self.parts).replace("\r", "")
        value = re.sub(r"[ \t]+", " ", value)
        value = re.sub(r"\n[ \t]+", "\n", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip()


def html_to_text(value: str) -> str:
    parser = _HTMLTextExtractor()
    try:
        parser.feed(value)
        parser.close()
        return parser.text()
    except Exception:
        return re.sub(r"<[^>]+>", "", value).strip()


def message_body_text(message: Message) -> str:
    html_value = ""
    for part in message.walk():
        disposition = str(part.get_content_disposition() or "")
        if disposition == "attachment":
            continue
        content_type = part.get_content_type()
        if content_type not in ("text/plain", "text/html"):
            continue
        try:
            content = part.get_content()
        except Exception:
            payload = part.get_payload(decode=True) or b""
            charset = part.get_content_charset() or "utf-8"
            content = payload.decode(charset, errors="replace")
        if content_type == "text/plain" and str(content).strip():
            return str(content).strip()
        if content_type == "text/html" and str(content).strip():
            html_value = str(content)
    return html_to_text(html_value) if html_value else "（邮件正文为空）"


def message_attachments(message: Message) -> list[tuple[str, Message]]:
    result: list[tuple[str, Message]] = []
    for index, part in enumerate(message.walk(), start=1):
        filename = decode_header_text(part.get_filename())
        if not filename and part.get_content_disposition() != "attachment":
            continue
        result.append((filename or f"附件-{index}", part))
    return result


@dataclass
class MailAccount:
    id: str
    label: str
    email: str
    display_name: str
    username: str
    provider: str
    imap_host: str
    imap_port: int
    imap_security: str
    smtp_host: str
    smtp_port: int
    smtp_security: str

    @classmethod
    def from_dict(cls, raw: dict) -> "MailAccount | None":
        try:
            account = cls(
                id=str(raw.get("id") or uuid.uuid4().hex),
                label=str(raw.get("label") or raw.get("email") or "邮箱").strip(),
                email=str(raw.get("email") or "").strip(),
                display_name=str(raw.get("display_name") or "").strip(),
                username=str(raw.get("username") or raw.get("email") or "").strip(),
                provider=str(raw.get("provider") or "自定义"),
                imap_host=str(raw.get("imap_host") or "").strip(),
                imap_port=int(raw.get("imap_port") or 993),
                imap_security=str(raw.get("imap_security") or "SSL/TLS"),
                smtp_host=str(raw.get("smtp_host") or "").strip(),
                smtp_port=int(raw.get("smtp_port") or 465),
                smtp_security=str(raw.get("smtp_security") or "SSL/TLS"),
            )
        except (TypeError, ValueError):
            return None
        if not account.email or not account.imap_host or not account.smtp_host:
            return None
        return account


@dataclass
class MailFolder:
    raw_name: str
    display_name: str
    flags: tuple[str, ...]


@dataclass
class MailSummary:
    uid: str
    subject: str
    sender: str
    sender_email: str
    date_text: str
    timestamp: float
    unread: bool
    size: int = 0
    flagged: bool = False


def mail_directory(data_dir: Path | str) -> Path:
    return Path(data_dir) / "Mail"


def load_mail_accounts(data_dir: Path | str) -> list[MailAccount]:
    accounts_file = mail_directory(data_dir) / "accounts.json"
    try:
        raw = json.loads(accounts_file.read_text(encoding="utf-8"))
    except Exception:
        raw = []
    accounts: list[MailAccount] = []
    for item in raw if isinstance(raw, list) else []:
        account = MailAccount.from_dict(item) if isinstance(item, dict) else None
        if account is not None:
            accounts.append(account)
    return accounts


def save_mail_accounts(data_dir: Path | str, accounts: list[MailAccount]) -> None:
    directory = mail_directory(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    accounts_file = directory / "accounts.json"
    temporary = directory / f"accounts-{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text(
            json.dumps([asdict(item) for item in accounts], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, accounts_file)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def resolve_mail_account(accounts: list[MailAccount], spec: dict) -> MailAccount | None:
    identifier = str(
        spec.get("account_id") or spec.get("account") or spec.get("email")
        or spec.get("label") or ""
    ).strip().casefold()
    if identifier:
        for account in accounts:
            if identifier in {
                account.id.casefold(), account.label.casefold(), account.email.casefold(),
                account.username.casefold(),
            }:
                return account
        return None
    return accounts[0] if len(accounts) == 1 else None


def _mail_password(account: MailAccount, supplied: str = "") -> str:
    password = str(supplied or "") or load_credential(account.id)
    if not password:
        raise RuntimeError(f"账户“{account.label}”没有可用的授权码或应用密码。")
    return password


def open_imap_account(account: MailAccount, password: str = ""):
    context = ssl.create_default_context()
    if account.imap_security == "SSL/TLS":
        client = imaplib.IMAP4_SSL(
            account.imap_host, account.imap_port, ssl_context=context, timeout=20,
        )
    else:
        client = imaplib.IMAP4(account.imap_host, account.imap_port, timeout=20)
        if account.imap_security == "STARTTLS":
            client.starttls(ssl_context=context)
    client.login(account.username, _mail_password(account, password))
    return client


def open_smtp_account(account: MailAccount, password: str = ""):
    context = ssl.create_default_context()
    if account.smtp_security == "SSL/TLS":
        client = smtplib.SMTP_SSL(
            account.smtp_host, account.smtp_port, timeout=25, context=context,
        )
    else:
        client = smtplib.SMTP(account.smtp_host, account.smtp_port, timeout=25)
        client.ehlo()
        if account.smtp_security == "STARTTLS":
            client.starttls(context=context)
            client.ehlo()
    client.login(account.username, _mail_password(account, password))
    return client


def _close_imap(client) -> None:
    try:
        client.logout()
    except Exception:
        pass


def _close_smtp(client) -> None:
    try:
        client.quit()
    except Exception:
        try:
            client.close()
        except Exception:
            pass


def _provider_name(value: str, email_value: str) -> str:
    raw = str(value or "").strip()
    folded = raw.casefold()
    aliases = {
        "qq": "QQ 邮箱", "qq邮箱": "QQ 邮箱", "qq 邮箱": "QQ 邮箱",
        "163": "163 邮箱", "163邮箱": "163 邮箱", "163 邮箱": "163 邮箱",
        "gmail": "Gmail", "google": "Gmail",
        "outlook": "Outlook / Hotmail", "hotmail": "Outlook / Hotmail",
        "outlook / hotmail": "Outlook / Hotmail",
        "custom": "自定义", "自定义": "自定义",
    }
    if folded in aliases:
        return aliases[folded]
    for name in PROVIDER_PRESETS:
        if folded == name.casefold():
            return name
    domain = email_value.rsplit("@", 1)[-1].casefold() if "@" in email_value else ""
    if domain == "qq.com":
        return "QQ 邮箱"
    if domain in {"163.com", "126.com", "yeah.net"}:
        return "163 邮箱"
    if domain == "gmail.com":
        return "Gmail"
    if domain in {"outlook.com", "hotmail.com", "live.com", "msn.com"}:
        return "Outlook / Hotmail"
    return raw if raw in PROVIDER_PRESETS else "自定义"


def _mail_security(value, fallback: str) -> str:
    folded = str(value or fallback).strip().casefold().replace(" ", "")
    if folded in {"ssl", "tls", "ssl/tls", "ssltls"}:
        return "SSL/TLS"
    if folded in {"starttls", "start-tls"}:
        return "STARTTLS"
    if folded in {"none", "plain", "无", "不加密"}:
        return "无"
    raise ValueError("安全方式仅支持 SSL/TLS、STARTTLS 或无。")


def _mail_folder_kind(folder: MailFolder) -> str:
    flags = set(folder.flags)
    if folder.raw_name.casefold() == "inbox" or "\\inbox" in flags:
        return "inbox"
    for flag, kind in (("\\sent", "sent"), ("\\drafts", "drafts"),
                       ("\\junk", "junk"), ("\\trash", "trash")):
        if flag in flags:
            return kind
    return "other"


class MailActionService:
    """Headless mail operations used by Aira's action protocol."""

    def __init__(self, data_dir: Path | str):
        self.data_dir = Path(data_dir)

    def accounts(self) -> list[MailAccount]:
        return load_mail_accounts(self.data_dir)

    def require_account(self, spec: dict) -> MailAccount:
        accounts = self.accounts()
        account = resolve_mail_account(accounts, spec)
        if account is not None:
            return account
        if not accounts:
            raise RuntimeError("尚未配置邮件账户，请先使用 mail_configure_account。")
        raise RuntimeError("未找到指定邮件账户；有多个账户时请提供 account、account_id 或 email。")

    @staticmethod
    def _list_folders(client) -> list[MailFolder]:
        status, data = client.list()
        if status != "OK":
            raise RuntimeError("无法读取邮箱文件夹。")
        folders: list[MailFolder] = []
        for raw in data or []:
            parsed = parse_folder_line(raw)
            if parsed is None:
                continue
            raw_name, display_name, flags = parsed
            if "\\noselect" not in flags:
                folders.append(MailFolder(raw_name, display_name, flags))
        folders.sort(key=lambda item: ({"inbox": 0, "sent": 1, "drafts": 2,
                                       "junk": 3, "trash": 4}.get(_mail_folder_kind(item), 10),
                                      item.display_name.casefold()))
        return folders

    @staticmethod
    def _resolve_folder(folders: list[MailFolder], requested: str) -> MailFolder:
        target = str(requested or "收件箱").strip().casefold()
        aliases = {
            "收件箱": "inbox", "inbox": "inbox",
            "已发送": "sent", "发件箱": "sent", "sent": "sent",
            "草稿": "drafts", "drafts": "drafts",
            "垃圾邮件": "junk", "junk": "junk", "spam": "junk",
            "已删除": "trash", "回收站": "trash", "trash": "trash",
        }
        wanted_kind = aliases.get(target)
        if wanted_kind:
            found = next((item for item in folders if _mail_folder_kind(item) == wanted_kind), None)
            if found is not None:
                return found
        found = next(
            (item for item in folders
             if target in {item.raw_name.casefold(), item.display_name.casefold()}),
            None,
        )
        if found is None:
            available = "、".join(item.display_name for item in folders[:20]) or "无"
            raise RuntimeError(f"未找到邮件文件夹“{requested or '收件箱'}”；可用：{available}")
        return found

    def list_accounts(self) -> str:
        accounts = self.accounts()
        payload = [
            {
                "id": item.id, "label": item.label, "email": item.email,
                "display_name": item.display_name, "provider": item.provider,
                "username": item.username,
                "imap": {"host": item.imap_host, "port": item.imap_port, "security": item.imap_security},
                "smtp": {"host": item.smtp_host, "port": item.smtp_port, "security": item.smtp_security},
                "credential_saved": bool(load_credential(item.id)),
            }
            for item in accounts
        ]
        return json.dumps({"accounts": payload, "count": len(payload)}, ensure_ascii=False, indent=2)

    def configure_account(self, spec: dict) -> str:
        accounts = self.accounts()
        existing = resolve_mail_account(accounts, spec)
        email_value = str(spec.get("email") or (existing.email if existing else "")).strip()
        if "@" not in email_value:
            raise ValueError("mail_configure_account 需要有效的 email。")
        provider = _provider_name(
            str(spec.get("provider") or (existing.provider if existing else "")), email_value,
        )
        preset = PROVIDER_PRESETS.get(provider, {})

        def text_value(key: str, fallback: str = "", *aliases: str) -> str:
            for name in (key, *aliases):
                if spec.get(name) is not None:
                    return str(spec.get(name) or "").strip()
            return fallback

        def port_value(key: str, fallback: int, *aliases: str) -> int:
            raw = next((spec.get(name) for name in (key, *aliases) if spec.get(name) is not None), fallback)
            value = int(raw)
            if not 1 <= value <= 65535:
                raise ValueError(f"{key} 必须在 1 到 65535 之间。")
            return value

        imap_host = text_value(
            "imap_host", str(preset.get("imap_host") or (existing.imap_host if existing else "")),
            "imap_server",
        )
        smtp_host = text_value(
            "smtp_host", str(preset.get("smtp_host") or (existing.smtp_host if existing else "")),
            "smtp_server",
        )
        if not imap_host or not smtp_host:
            raise ValueError("自定义邮箱需要提供 imap_host 和 smtp_host。")
        account = MailAccount(
            id=existing.id if existing else str(spec.get("account_id") or uuid.uuid4().hex),
            label=text_value("label", existing.label if existing else email_value) or email_value,
            email=email_value,
            display_name=text_value("display_name", existing.display_name if existing else "", "sender_name"),
            username=text_value("username", existing.username if existing else email_value) or email_value,
            provider=provider,
            imap_host=imap_host,
            imap_port=port_value("imap_port", int(preset.get("imap_port") or (existing.imap_port if existing else 993))),
            imap_security=_mail_security(
                spec.get("imap_security"),
                str(preset.get("imap_security") or (existing.imap_security if existing else "SSL/TLS")),
            ),
            smtp_host=smtp_host,
            smtp_port=port_value("smtp_port", int(preset.get("smtp_port") or (existing.smtp_port if existing else 465))),
            smtp_security=_mail_security(
                spec.get("smtp_security"),
                str(preset.get("smtp_security") or (existing.smtp_security if existing else "SSL/TLS")),
            ),
        )
        password = text_value("password", "", "app_password", "authorization_code", "auth_code")
        if existing is None and not password:
            raise ValueError("新增邮件账户需要 password（授权码或应用密码）。")
        if password and not WIN_CREDENTIALS_AVAILABLE:
            raise RuntimeError("Windows 凭据管理器不可用，无法安全保存邮件授权码。")
        if existing is None:
            accounts.append(account)
        else:
            accounts[accounts.index(existing)] = account
        save_mail_accounts(self.data_dir, accounts)
        if password:
            save_credential(account.id, account.username, password)
            if not load_credential(account.id):
                raise RuntimeError("账户配置已写入，但授权码未能保存到 Windows 凭据管理器。")
        verb = "更新" if existing else "新增"
        return f"已{verb}邮件账户“{account.label}” <{account.email}>；授权码未写入普通配置文件。"

    def remove_account(self, spec: dict) -> str:
        accounts = self.accounts()
        account = resolve_mail_account(accounts, spec)
        if account is None:
            raise RuntimeError("未找到要移除的邮件账户。")
        save_mail_accounts(self.data_dir, [item for item in accounts if item.id != account.id])
        delete_credential(account.id)
        return f"已移除邮件账户“{account.label}”及其系统凭据。"

    def test_account(self, spec: dict) -> str:
        account = self.require_account(spec)
        password = str(spec.get("password") or spec.get("app_password") or "")
        imap = open_imap_account(account, password)
        try:
            imap.noop()
        finally:
            _close_imap(imap)
        smtp = open_smtp_account(account, password)
        try:
            smtp.noop()
        finally:
            _close_smtp(smtp)
        return f"账户“{account.label}”的 IMAP 与 SMTP 连接均成功。"

    def list_folders(self, spec: dict) -> str:
        account = self.require_account(spec)
        client = open_imap_account(account)
        try:
            folders = self._list_folders(client)
        finally:
            _close_imap(client)
        payload = [
            {"name": item.display_name, "raw_name": item.raw_name, "kind": _mail_folder_kind(item)}
            for item in folders
        ]
        return json.dumps(
            {"account": account.email, "folders": payload, "count": len(payload)},
            ensure_ascii=False, indent=2,
        )

    def list_messages(self, spec: dict) -> str:
        account = self.require_account(spec)
        limit = max(1, min(50, int(spec.get("limit") or 20)))
        query = str(spec.get("query") or spec.get("search") or "").strip().casefold()
        client = open_imap_account(account)
        try:
            folders = self._list_folders(client)
            folder = self._resolve_folder(folders, str(spec.get("folder") or "收件箱"))
            status, _ = client.select(quote_mailbox(folder.raw_name), readonly=True)
            if status != "OK":
                raise RuntimeError(f"无法打开文件夹：{folder.display_name}")
            status, data = client.uid("search", None, "ALL")
            if status != "OK" or not data:
                raise RuntimeError("无法搜索邮件列表。")
            uids = data[0].split()[-MAX_MESSAGES:]
            if not uids:
                payload: list[dict] = []
            else:
                status, fetched = client.uid(
                    "fetch", b",".join(uids),
                    "(UID FLAGS RFC822.SIZE BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)])",
                )
                if status != "OK":
                    raise RuntimeError("无法读取邮件列表。")
                by_uid: dict[str, dict] = {}
                for item in fetched or []:
                    if not isinstance(item, tuple) or len(item) < 2:
                        continue
                    meta = item[0].decode("ascii", errors="ignore") if isinstance(item[0], bytes) else str(item[0])
                    uid_match = re.search(r"\bUID\s+(\d+)", meta, re.I)
                    if not uid_match:
                        continue
                    header = BytesParser(policy=policy.default).parsebytes(
                        item[1] if isinstance(item[1], bytes) else bytes(item[1] or b""),
                    )
                    sender_value = decode_header_text(header.get("From"))
                    sender_name, sender_email = parseaddr(sender_value)
                    subject = decode_header_text(header.get("Subject")) or "（无主题）"
                    date_value = str(header.get("Date") or "")
                    try:
                        parsed = parsedate_to_datetime(date_value)
                        date_value = parsed.astimezone().isoformat(timespec="minutes") if parsed else date_value
                    except Exception:
                        pass
                    size_match = re.search(r"RFC822\.SIZE\s+(\d+)", meta, re.I)
                    row = {
                        "uid": uid_match.group(1), "subject": subject,
                        "sender": sender_name or sender_email or sender_value or "未知发件人",
                        "sender_email": sender_email, "date": date_value,
                        "unread": "\\seen" not in meta.casefold(),
                        "flagged": "\\flagged" in meta.casefold(),
                        "size": int(size_match.group(1)) if size_match else 0,
                    }
                    haystack = f"{subject}\n{sender_value}\n{decode_header_text(header.get('To'))}".casefold()
                    if not query or query in haystack:
                        by_uid[row["uid"]] = row
                payload = [by_uid[uid.decode("ascii")] for uid in reversed(uids)
                           if uid.decode("ascii") in by_uid][:limit]
        finally:
            _close_imap(client)
        return json.dumps(
            {"account": account.email, "folder": folder.display_name,
             "messages": payload, "count": len(payload)},
            ensure_ascii=False, indent=2,
        )

    def read_message(self, spec: dict) -> str:
        account = self.require_account(spec)
        uid = str(spec.get("uid") or "").strip()
        if not uid.isdigit():
            raise ValueError("mail_read_message 需要 mail_list_messages 返回的数字 uid。")
        max_chars = max(1000, min(50000, int(spec.get("max_chars") or 12000)))
        client = open_imap_account(account)
        try:
            folders = self._list_folders(client)
            folder = self._resolve_folder(folders, str(spec.get("folder") or "收件箱"))
            status, _ = client.select(quote_mailbox(folder.raw_name), readonly=True)
            if status != "OK":
                raise RuntimeError(f"无法打开文件夹：{folder.display_name}")
            status, data = client.uid("fetch", uid, "(UID BODY.PEEK[])")
            if status != "OK":
                raise RuntimeError("无法读取邮件正文。")
            raw = next(
                (item[1] for item in data or []
                 if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], bytes)),
                None,
            )
            if raw is None:
                raise RuntimeError(f"未找到 UID {uid} 的邮件。")
            message = BytesParser(policy=policy.default).parsebytes(raw)
        finally:
            _close_imap(client)
        body = message_body_text(message)
        clipped = len(body) > max_chars
        attachments = [
            {"name": name, "size": len(part.get_payload(decode=True) or b"")}
            for name, part in message_attachments(message)
        ]
        payload = {
            "account": account.email, "folder": folder.display_name, "uid": uid,
            "subject": decode_header_text(message.get("Subject")) or "（无主题）",
            "from": decode_header_text(message.get("From")),
            "to": decode_header_text(message.get("To")),
            "cc": decode_header_text(message.get("Cc")),
            "date": str(message.get("Date") or ""),
            "body": body[:max_chars], "body_truncated": clipped,
            "attachments": attachments,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    @staticmethod
    def _addresses(value) -> list[str]:
        if isinstance(value, (list, tuple)):
            source = [str(item) for item in value]
        else:
            source = [str(value or "").replace(";", ",")]
        return [address for _name, address in getaddresses(source) if address]

    def send_message(self, spec: dict) -> str:
        account = self.require_account(spec)
        to_addresses = self._addresses(spec.get("to") or spec.get("recipient"))
        cc_addresses = self._addresses(spec.get("cc"))
        bcc_addresses = self._addresses(spec.get("bcc"))
        if not to_addresses:
            raise ValueError("mail_send 需要有效的 to 收件人地址。")
        subject = str(spec.get("subject") or "").strip() or "（无主题）"
        body = str(spec.get("body") or spec.get("content") or spec.get("message") or "")
        raw_attachments = spec.get("attachments") or spec.get("files") or []
        if isinstance(raw_attachments, (str, Path)):
            raw_attachments = [raw_attachments]
        paths = [Path(str(value)).expanduser() for value in raw_attachments]
        if len(paths) > 20:
            raise ValueError("单封邮件最多添加 20 个附件。")
        total_size = 0
        for path in paths:
            if not path.is_file():
                raise FileNotFoundError(f"附件不存在：{path}")
            total_size += path.stat().st_size
        if total_size > 50 * 1024 * 1024:
            raise ValueError("附件总大小超过 50 MB。")
        message = EmailMessage()
        message["From"] = formataddr((account.display_name, account.email)) if account.display_name else account.email
        message["To"] = ", ".join(to_addresses)
        if cc_addresses:
            message["Cc"] = ", ".join(cc_addresses)
        message["Subject"] = subject
        message.set_content(body)
        for path in paths:
            mime_type, _encoding = mimetypes.guess_type(str(path))
            main_type, sub_type = (mime_type or "application/octet-stream").split("/", 1)
            message.add_attachment(path.read_bytes(), maintype=main_type, subtype=sub_type, filename=path.name)
        smtp = open_smtp_account(account)
        try:
            smtp.send_message(message, to_addrs=to_addresses + cc_addresses + bcc_addresses)
        finally:
            _close_smtp(smtp)
        return f"邮件已通过“{account.label}”发送给：{', '.join(to_addresses)}。"

    def set_status(self, spec: dict) -> str:
        account = self.require_account(spec)
        uid = str(spec.get("uid") or "").strip()
        if not uid.isdigit():
            raise ValueError("mail_set_status 需要数字 uid。")
        state = str(spec.get("state") or spec.get("status") or "").strip().casefold()
        operations = {
            "read": ("+FLAGS.SILENT", "(\\Seen)", "已读"),
            "已读": ("+FLAGS.SILENT", "(\\Seen)", "已读"),
            "unread": ("-FLAGS.SILENT", "(\\Seen)", "未读"),
            "未读": ("-FLAGS.SILENT", "(\\Seen)", "未读"),
            "flagged": ("+FLAGS.SILENT", "(\\Flagged)", "重要"),
            "important": ("+FLAGS.SILENT", "(\\Flagged)", "重要"),
            "重要": ("+FLAGS.SILENT", "(\\Flagged)", "重要"),
            "unflagged": ("-FLAGS.SILENT", "(\\Flagged)", "非重要"),
            "not_important": ("-FLAGS.SILENT", "(\\Flagged)", "非重要"),
            "取消重要": ("-FLAGS.SILENT", "(\\Flagged)", "非重要"),
        }
        if state not in operations:
            raise ValueError("state 仅支持 read、unread、flagged 或 unflagged。")
        operator, flag, label = operations[state]
        client = open_imap_account(account)
        try:
            folders = self._list_folders(client)
            folder = self._resolve_folder(folders, str(spec.get("folder") or "收件箱"))
            status, _ = client.select(quote_mailbox(folder.raw_name), readonly=False)
            if status != "OK":
                raise RuntimeError(f"无法打开文件夹：{folder.display_name}")
            status, _ = client.uid("store", uid, operator, flag)
            if status != "OK":
                raise RuntimeError("邮件服务器未接受状态修改。")
        finally:
            _close_imap(client)
        return f"已将 UID {uid} 标记为{label}。"

    def delete_message(self, spec: dict) -> str:
        account = self.require_account(spec)
        uid = str(spec.get("uid") or "").strip()
        if not uid.isdigit():
            raise ValueError("mail_delete_message 需要数字 uid。")
        client = open_imap_account(account)
        try:
            folders = self._list_folders(client)
            folder = self._resolve_folder(folders, str(spec.get("folder") or "收件箱"))
            status, _ = client.select(quote_mailbox(folder.raw_name), readonly=False)
            if status != "OK":
                raise RuntimeError(f"无法打开文件夹：{folder.display_name}")
            status, _ = client.uid("store", uid, "+FLAGS.SILENT", "(\\Deleted)")
            if status != "OK":
                raise RuntimeError("邮件服务器未接受删除操作。")
            client.expunge()
        finally:
            _close_imap(client)
        return f"已删除 {folder.display_name} 中 UID {uid} 的邮件。"


def run_mail_action(data_dir: Path | str, spec: dict, action: str) -> str:
    service = MailActionService(data_dir)
    action = str(action or "").strip().lower()
    if action in {"mail_list_accounts", "email_list_accounts"}:
        return service.list_accounts()
    if action in {"mail_configure_account", "mail_add_account", "mail_update_account",
                  "email_configure_account"}:
        return service.configure_account(spec)
    if action in {"mail_remove_account", "email_remove_account"}:
        return service.remove_account(spec)
    if action in {"mail_test_account", "email_test_account"}:
        return service.test_account(spec)
    if action in {"mail_list_folders", "email_list_folders"}:
        return service.list_folders(spec)
    if action in {"mail_list_messages", "mail_search", "email_list_messages", "email_search"}:
        return service.list_messages(spec)
    if action in {"mail_read_message", "email_read_message"}:
        return service.read_message(spec)
    if action in {"mail_send", "mail_send_message", "email_send"}:
        return service.send_message(spec)
    if action in {"mail_set_status", "email_set_status"}:
        return service.set_status(spec)
    if action in {"mail_delete_message", "email_delete_message"}:
        return service.delete_message(spec)
    raise ValueError(f"不支持的邮件动作：{action}")


def bind_wheel_scroll(widget: tk.Widget) -> None:
    """Keep vertical scrolling available without reserving space for a scrollbar."""
    def scroll(event: tk.Event):
        try:
            if getattr(event, "num", None) == 4:
                units = -1
            elif getattr(event, "num", None) == 5:
                units = 1
            else:
                delta = int(getattr(event, "delta", 0) or 0)
                if not delta:
                    return None
                units = -max(1, abs(delta) // 120) if delta > 0 else max(1, abs(delta) // 120)
            widget.yview_scroll(units, tk.UNITS)
        except (AttributeError, tk.TclError):
            return None
        return "break"

    widget.bind("<MouseWheel>", scroll, add="+")
    widget.bind("<Button-4>", scroll, add="+")
    widget.bind("<Button-5>", scroll, add="+")


class PasserDropdown(tk.Frame):
    """Passer-styled readonly selector backed by a themed popup menu."""
    def __init__(self, parent, variable: tk.StringVar, values, theme: ClickerTheme,
                 font, command: Callable[[], None] | None = None,
                 width: int = 200, height: int = 34):
        super().__init__(
            parent,
            bg="#ffffff",
            highlightthickness=1,
            highlightbackground=theme.border,
            highlightcolor=theme.accent,
            width=width,
            height=height,
            cursor="hand2",
            takefocus=True,
        )
        self.variable = variable
        self.values = tuple(str(value) for value in values)
        self.theme = theme
        self.command = command
        self.pack_propagate(False)
        self.value_label = tk.Label(
            self,
            textvariable=variable,
            bg="#ffffff",
            fg="#1f2937",
            anchor=tk.W,
            font=font,
            cursor="hand2",
        )
        self.value_label.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 3))
        self.arrow_label = tk.Label(
            self,
            text="▾",
            bg="#ffffff",
            fg=theme.muted_fg,
            font=font,
            cursor="hand2",
        )
        self.arrow_label.pack(side=tk.RIGHT, padx=(3, 9))
        for child in (self, self.value_label, self.arrow_label):
            child.bind("<Button-1>", self.open_menu)
            child.bind("<Enter>", self._hover_on)
            child.bind("<Leave>", self._hover_off)
        self.bind("<Return>", self.open_menu)
        self.bind("<space>", self.open_menu)
        self.bind("<Down>", self.open_menu)
        self.bind("<FocusIn>", self._focus_on)
        self.bind("<FocusOut>", self._focus_off)

    def set_values(self, values) -> None:
        self.values = tuple(str(value) for value in values)

    def _set_surface(self, color: str) -> None:
        self.configure(bg=color)
        self.value_label.configure(bg=color)
        self.arrow_label.configure(bg=color)

    def _hover_on(self, _event=None) -> None:
        self.configure(highlightbackground=self.theme.accent)
        self._set_surface(self.theme.accent_soft)

    def _hover_off(self, _event=None) -> None:
        if self.focus_get() is not self:
            self.configure(highlightbackground=self.theme.border)
        self._set_surface("#ffffff")

    def _focus_on(self, _event=None) -> None:
        self.configure(highlightbackground=self.theme.accent)

    def _focus_off(self, _event=None) -> None:
        self.configure(highlightbackground=self.theme.border)

    def _choose(self, value: str) -> None:
        self.variable.set(value)
        self.event_generate("<<ComboboxSelected>>")
        if self.command is not None:
            self.command()

    def open_menu(self, _event=None):
        if not self.values:
            return "break"
        self.focus_set()
        menu = tk.Menu(
            self,
            tearoff=False,
            bg="#ffffff",
            fg="#1f2937",
            activebackground=self.theme.accent,
            activeforeground="#ffffff",
            activeborderwidth=0,
            bd=1,
            relief=tk.SOLID,
            font=self.value_label.cget("font"),
            cursor="hand2",
        )
        current = self.variable.get()
        for value in self.values:
            label = f"✓  {value}" if value == current else f"    {value}"
            menu.add_command(label=label, command=lambda selected=value: self._choose(selected))
        try:
            menu.tk_popup(self.winfo_rootx(), self.winfo_rooty() + self.winfo_height())
        finally:
            menu.grab_release()
            menu.destroy()
        return "break"


class MailWindow:
    CHROME_TOP = 46
    CHROME_BOTTOM = 16
    WIDTH = 1888
    HEIGHT = 1216

    def __init__(self, app, theme: ClickerTheme):
        self.app = app
        self.theme = theme
        self.closed = False
        self.move_start = None
        self._minimize_in_progress = False
        self._frame_restore_after_id = None
        self.accounts: list[MailAccount] = self._load_accounts()
        self.account_by_label: dict[str, MailAccount] = {}
        self.folders: list[MailFolder] = []
        self.folder_by_iid: dict[str, MailFolder] = {}
        self.summaries: list[MailSummary] = []
        self.summary_by_iid: dict[str, MailSummary] = {}
        self.contact_by_iid: dict[str, str] = {}
        self.current_folder: MailFolder | None = None
        self.current_message: Message | None = None
        self.current_summary: MailSummary | None = None
        self.current_attachments: list[tuple[str, Message]] = []
        self.compose_windows: list[ComposeWindow] = []
        self.load_generation = 0

        self.account_var = tk.StringVar(value="")
        self.search_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="就绪")
        self.subject_var = tk.StringVar(value="选择一封邮件")
        self.meta_var = tk.StringVar(value="")
        self.view_title_var = tk.StringVar(value="收件箱")

        self.window = tk.Toplevel(app.root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.configure(bg=theme.border)
        self.window.minsize(self.WIDTH, self.HEIGHT)
        self.window.maxsize(self.WIDTH, self.HEIGHT)
        self.window.resizable(False, False)
        self.shell = tk.Frame(self.window, bg=theme.surface_bg, highlightthickness=1, highlightbackground=theme.border)
        self.shell.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        self._configure_styles()
        self._build_chrome()
        self._build_body()
        self._refresh_account_menu()
        self.show_message_list()

        self.window.bind("<Escape>", lambda _event: self.close())
        self.window.bind("<Map>", self._restore_custom_frame, add="+")
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.search_var.trace_add("write", lambda *_args: self._render_messages())
        self._place_on_passer()
        self.window.attributes("-topmost", app.topmost_var.get())
        app.apply_window_transparency(self.window)
        self.window.deiconify()
        self.window.focus_force()
        if self.accounts:
            self.window.after(120, self.refresh_folders)
        else:
            self.window.after(160, self.open_account_dialog)

    @property
    def mail_dir(self) -> Path:
        base = Path(getattr(self.app, "data_dir", Path.home() / "PasserData"))
        return base / "Mail"

    @property
    def accounts_file(self) -> Path:
        return self.mail_dir / "accounts.json"

    def _font(self, size: int = 9, weight: str = "normal"):
        return self.theme.app_font(size, weight)

    def _configure_styles(self) -> None:
        style = ttk.Style(self.window)
        style.configure(
            "Mail.Treeview",
            background="#ffffff",
            fieldbackground="#ffffff",
            foreground="#1f2937",
            borderwidth=0,
            rowheight=43,
            font=self._font(9),
        )
        style.map("Mail.Treeview", background=[("selected", self.theme.accent_soft)], foreground=[("selected", self.theme.accent)])
        style.configure("Mail.Treeview.Heading", background="#eef2f7", foreground="#475569", relief=tk.FLAT, font=self._font(8, "bold"))
        style.configure(
            "MailFolders.Treeview",
            background="#f8fafc",
            fieldbackground="#f8fafc",
            foreground="#334155",
            borderwidth=0,
            rowheight=42,
            font=self._font(9),
        )
        style.map("MailFolders.Treeview", background=[("selected", self.theme.accent_soft)], foreground=[("selected", self.theme.accent)])
        style.configure(
            "MailContacts.Treeview",
            background="#f8fafc",
            fieldbackground="#f8fafc",
            foreground="#475569",
            borderwidth=0,
            rowheight=35,
            font=self._font(9),
        )
        style.map("MailContacts.Treeview", background=[("selected", self.theme.accent_soft)], foreground=[("selected", self.theme.accent)])

    def _place_on_passer(self) -> None:
        try:
            x, y = self.theme.center_over_root(self.app.root, self.WIDTH, self.HEIGHT)
            self.theme.place_toplevel_absolute(self.window, self.WIDTH, self.HEIGHT, x, y)
        except Exception:
            self.window.geometry(f"{self.WIDTH}x{self.HEIGHT}")

    def _build_chrome(self) -> None:
        bar = tk.Frame(self.shell, bg=self.theme.title_bg, height=self.CHROME_TOP)
        bar.pack(side=tk.TOP, fill=tk.X)
        bar.pack_propagate(False)
        title = tk.Label(bar, text=self.theme.title, bg=self.theme.title_bg, fg="#dbe7ff", anchor=tk.W, font=self._font(10, "bold"))
        title.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(14, 8))
        self._chrome_button(bar, "×", self.close, close=True).pack(side=tk.RIGHT, padx=(0, 8), pady=8)
        self._chrome_button(bar, "—", self.minimize).pack(side=tk.RIGHT, padx=(0, 4), pady=8)
        for widget in (bar, title):
            widget.bind("<ButtonPress-1>", self.start_move)
            widget.bind("<B1-Motion>", self.do_move)

    def _build_body(self) -> None:
        body = tk.Frame(self.shell, bg=self.theme.surface_bg)
        body.pack(fill=tk.BOTH, expand=True)

        header = tk.Frame(body, bg=self.theme.surface_bg, height=68, highlightthickness=1, highlightbackground=self.theme.border)
        header.pack(fill=tk.X, padx=14, pady=(10, 0))
        header.pack_propagate(False)
        brand = tk.Frame(header, bg=self.theme.surface_bg)
        brand.pack(side=tk.LEFT, fill=tk.Y, padx=(14, 10))
        tk.Label(brand, text="PASSER MAIL", bg=self.theme.surface_bg, fg=self.theme.accent,
                 anchor=tk.W, font=self._font(12, "bold")).pack(anchor=tk.W, pady=(9, 0))
        tk.Label(brand, text="邮件中心", bg=self.theme.surface_bg, fg=self.theme.muted_fg,
                 anchor=tk.W, font=self._font(8)).pack(anchor=tk.W)
        self.account_select = self._select(
            header, self.account_var, (), command=self._account_changed, width=250, height=36,
        )
        self.account_select.pack(side=tk.LEFT)
        self._button(header, "账户", self.open_account_dialog).pack(side=tk.LEFT, padx=(7, 0))
        self._button(header, "写邮件", self.open_compose, primary=True).pack(side=tk.RIGHT, padx=(8, 14))
        self.search_entry = self._entry(header, self.search_var, width=27)
        self.search_entry.pack(side=tk.RIGHT, ipady=6)
        self.search_entry.bind("<FocusIn>", lambda _event: self.show_message_list())
        tk.Label(header, text="搜索", bg=self.theme.surface_bg, fg=self.theme.muted_fg,
                 font=self._font(9)).pack(side=tk.RIGHT, padx=(0, 7))

        panes = tk.PanedWindow(body, orient=tk.HORIZONTAL, sashwidth=1, sashrelief=tk.FLAT, bd=0, bg=self.theme.border, showhandle=False)
        panes.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 8))

        sidebar = tk.Frame(panes, bg="#f8fafc", width=238, highlightthickness=1, highlightbackground=self.theme.border)
        sidebar.pack_propagate(False)
        panes.add(sidebar, minsize=220, width=238, stretch="never")
        mail_head = tk.Frame(sidebar, bg="#f8fafc", height=54)
        mail_head.pack(fill=tk.X)
        mail_head.pack_propagate(False)
        tk.Label(mail_head, text="✉", bg="#f8fafc", fg=self.theme.accent,
                 font=self._font(14)).pack(side=tk.LEFT, padx=(14, 9))
        tk.Label(mail_head, text="邮件", bg="#f8fafc", fg="#0f172a", anchor=tk.W,
                 font=self._font(11, "bold")).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._button(mail_head, "刷新", self.refresh_current).pack(side=tk.RIGHT, padx=(0, 8), pady=9)
        folder_wrap = tk.Frame(sidebar, bg="#f8fafc")
        folder_wrap.pack(fill=tk.X, padx=4, pady=(0, 6))
        self.folder_tree = ttk.Treeview(folder_wrap, show="tree", style="MailFolders.Treeview", selectmode="browse", height=7)
        self.folder_tree.pack(fill=tk.X, expand=True)
        bind_wheel_scroll(self.folder_tree)
        self.folder_tree.bind("<<TreeviewSelect>>", self._folder_selected)
        separator = tk.Frame(sidebar, bg=self.theme.border, height=1)
        separator.pack(fill=tk.X, pady=(2, 0))
        contacts_head = tk.Frame(sidebar, bg="#f8fafc", height=48)
        contacts_head.pack(fill=tk.X)
        contacts_head.pack_propagate(False)
        tk.Label(contacts_head, text="联系人", bg="#f8fafc", fg="#0f172a", anchor=tk.W,
                 font=self._font(10, "bold")).pack(side=tk.LEFT, padx=14)
        tk.Label(contacts_head, text="按发件人", bg="#f8fafc", fg=self.theme.muted_fg,
                 font=self._font(8)).pack(side=tk.RIGHT, padx=12)
        contact_wrap = tk.Frame(sidebar, bg="#f8fafc")
        contact_wrap.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 6))
        self.contact_tree = ttk.Treeview(contact_wrap, show="tree", style="MailContacts.Treeview", selectmode="browse")
        self.contact_tree.pack(fill=tk.BOTH, expand=True)
        bind_wheel_scroll(self.contact_tree)
        self.contact_tree.bind("<<TreeviewSelect>>", self._contact_selected)

        content = tk.Frame(panes, bg="#ffffff", highlightthickness=1, highlightbackground=self.theme.border)
        panes.add(content, minsize=720, stretch="always")
        content_toolbar = tk.Frame(content, bg="#ffffff", height=54)
        content_toolbar.pack(fill=tk.X)
        content_toolbar.pack_propagate(False)
        self.back_button = self._button(content_toolbar, "‹", self.show_message_list)
        self.back_button.pack(side=tk.LEFT, padx=(10, 6), pady=9)
        tk.Label(content_toolbar, textvariable=self.view_title_var, bg="#ffffff", fg="#0f172a",
                 anchor=tk.W, font=self._font(10, "bold")).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.next_button = self._button(content_toolbar, "›", lambda: self.navigate_message(1))
        self.next_button.pack(side=tk.RIGHT, padx=(5, 10), pady=9)
        self.prev_button = self._button(content_toolbar, "‹", lambda: self.navigate_message(-1))
        self.prev_button.pack(side=tk.RIGHT, pady=9)
        tk.Frame(content, bg=self.theme.border, height=1).pack(fill=tk.X)

        self.content_stack = tk.Frame(content, bg="#ffffff")
        self.content_stack.pack(fill=tk.BOTH, expand=True)
        self.list_view = tk.Frame(self.content_stack, bg="#ffffff")
        self.list_view.pack(fill=tk.BOTH, expand=True)
        list_head = tk.Frame(self.list_view, bg="#ffffff", height=48)
        list_head.pack(fill=tk.X, padx=14)
        list_head.pack_propagate(False)
        tk.Label(list_head, text="邮件列表", bg="#ffffff", fg="#334155", anchor=tk.W,
                 font=self._font(9, "bold")).pack(side=tk.LEFT, fill=tk.Y)
        self.message_count_var = tk.StringVar(value="0 封")
        tk.Label(list_head, textvariable=self.message_count_var, bg="#ffffff", fg=self.theme.muted_fg,
                 font=self._font(8)).pack(side=tk.RIGHT, fill=tk.Y)
        self.message_tree = ttk.Treeview(
            self.list_view,
            columns=("sender", "date"),
            show="tree headings",
            style="Mail.Treeview",
            selectmode="browse",
        )
        self.message_tree.heading("#0", text="主题")
        self.message_tree.heading("sender", text="发件人")
        self.message_tree.heading("date", text="日期")
        self.message_tree.column("#0", width=470, minwidth=260, stretch=True)
        self.message_tree.column("sender", width=190, minwidth=120, stretch=False)
        self.message_tree.column("date", width=120, minwidth=95, stretch=False, anchor=tk.E)
        self.message_tree.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 12))
        bind_wheel_scroll(self.message_tree)
        self.message_tree.bind("<<TreeviewSelect>>", self._message_selected)

        self.reader_view = tk.Frame(self.content_stack, bg="#ffffff")
        reader_head = tk.Frame(self.reader_view, bg="#ffffff")
        reader_head.pack(fill=tk.X, padx=28, pady=(24, 12))
        tk.Label(reader_head, textvariable=self.subject_var, bg="#ffffff", fg="#0f172a", anchor=tk.W,
                 justify=tk.LEFT, wraplength=780, font=self._font(16, "bold")).pack(fill=tk.X)
        tk.Label(reader_head, textvariable=self.meta_var, bg="#ffffff", fg=self.theme.muted_fg, anchor=tk.W,
                 justify=tk.LEFT, wraplength=780, font=self._font(9)).pack(fill=tk.X, pady=(10, 0))
        body_wrap = tk.Frame(self.reader_view, bg="#ffffff")
        body_wrap.pack(fill=tk.BOTH, expand=True, padx=28, pady=(2, 8))
        self.body_text = tk.Text(body_wrap, bd=0, relief=tk.FLAT, bg="#ffffff", fg="#1f2937", wrap=tk.WORD,
                                 padx=4, pady=14, spacing1=3, spacing3=7, font=self._font(10), state=tk.DISABLED)
        self.body_text.pack(fill=tk.BOTH, expand=True)
        bind_wheel_scroll(self.body_text)

        attachment_row = tk.Frame(self.reader_view, bg="#ffffff", height=54)
        attachment_row.pack(fill=tk.X, padx=28, pady=(0, 8))
        attachment_row.pack_propagate(False)
        self.attachment_list = tk.Listbox(attachment_row, height=2, bd=0, relief=tk.FLAT, bg="#f8fafc", fg="#334155",
                                          highlightthickness=1, highlightbackground=self.theme.border, font=self._font(8), exportselection=False)
        self.attachment_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._button(attachment_row, "保存附件", self.save_attachment).pack(side=tk.LEFT, padx=(8, 0))
        self._button(attachment_row, "打开附件", self.open_attachment).pack(side=tk.LEFT, padx=(6, 0))

        actions = tk.Frame(self.reader_view, bg="#f8fafc", height=58, highlightthickness=1, highlightbackground=self.theme.border)
        actions.pack(fill=tk.X, side=tk.BOTTOM)
        actions.pack_propagate(False)
        action_items = (
            ("标记", self.toggle_current_flag, False),
            ("删除", self.delete_current, True),
            ("回复", self.reply_current, False),
            ("转发", self.forward_current, False),
            ("更多", self.open_more_menu, False),
        )
        for index, (label, command, danger) in enumerate(action_items):
            actions.grid_columnconfigure(index, weight=1, uniform="mail_reader_actions")
            button = self._button(actions, label, command, danger=danger)
            button.grid(row=0, column=index, sticky="nsew", padx=(10 if index == 0 else 3, 10 if index == len(action_items) - 1 else 3), pady=10)
            if label == "更多":
                self.more_button = button

        status = tk.Label(body, textvariable=self.status_var, bg="#f8fafc", fg=self.theme.muted_fg, anchor=tk.W,
                          padx=12, pady=6, font=self._font(8), highlightthickness=1, highlightbackground=self.theme.border)
        status.pack(fill=tk.X, padx=14, pady=(0, 10))
        bottom = tk.Frame(self.shell, bg=self.theme.title_bg, height=self.CHROME_BOTTOM)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        bottom.pack_propagate(False)

    def _button(self, parent, text: str, command: Callable, primary: bool = False, danger: bool = False) -> tk.Button:
        background = self.theme.danger if danger else (self.theme.accent if primary else "#eef2f7")
        foreground = "#ffffff" if primary or danger else "#334155"
        active = "#dc2626" if danger else (self.theme.accent_hover if primary else self.theme.accent_soft_hover)
        return tk.Button(parent, text=text, command=command, bd=0, padx=11, pady=7, bg=background, fg=foreground,
                         activebackground=active, activeforeground="#ffffff" if primary or danger else self.theme.accent,
                         cursor="hand2", font=self._font(9, "bold" if primary else "normal"))

    def _entry(self, parent, variable: tk.StringVar, width: int = 20, show: str = "") -> tk.Entry:
        return tk.Entry(parent, textvariable=variable, width=width, show=show, bd=0, relief=tk.FLAT, bg="#ffffff",
                        fg="#1f2937", insertbackground=self.theme.accent, highlightthickness=1,
                        highlightbackground=self.theme.border, highlightcolor=self.theme.accent, font=self._font(9))

    def _select(self, parent, variable: tk.StringVar, values, command: Callable[[], None] | None = None,
                width: int = 200, height: int = 34, font_size: int = 9) -> PasserDropdown:
        return PasserDropdown(
            parent,
            variable,
            values,
            self.theme,
            self._font(font_size),
            command=command,
            width=width,
            height=height,
        )

    def _chrome_button(self, parent, text: str, command: Callable, close: bool = False) -> tk.Button:
        hover = "#ef4444" if close else self.theme.title_button_hover
        button = tk.Button(parent, text=text, command=command, bd=0, padx=11, pady=5, bg=self.theme.title_button_bg,
                           fg="#e7eefc", activebackground=hover, activeforeground="#ffffff", cursor="hand2", font=self._font(10))
        button.bind("<Enter>", lambda _event: button.configure(bg=hover))
        button.bind("<Leave>", lambda _event: button.configure(bg=self.theme.title_button_bg))
        return button

    def _load_accounts(self) -> list[MailAccount]:
        return load_mail_accounts(Path(getattr(self.app, "data_dir", Path.home() / "PasserData")))

    def _save_accounts(self) -> None:
        save_mail_accounts(Path(getattr(self.app, "data_dir", Path.home() / "PasserData")), self.accounts)

    def _refresh_account_menu(self, selected_id: str | None = None) -> None:
        self.account_by_label.clear()
        labels: list[str] = []
        for account in self.accounts:
            base = account.label or account.email
            label = base
            counter = 2
            while label in self.account_by_label:
                label = f"{base} ({counter})"
                counter += 1
            labels.append(label)
            self.account_by_label[label] = account
        self.account_select.set_values(labels)
        target = next((label for label, account in self.account_by_label.items() if account.id == selected_id), "")
        if not target and self.account_var.get() in self.account_by_label:
            target = self.account_var.get()
        self.account_var.set(target or (labels[0] if labels else "未配置账户"))

    def current_account(self) -> MailAccount | None:
        return self.account_by_label.get(self.account_var.get())

    def reload_accounts(self, selected_id: str | None = None, refresh: bool = True) -> None:
        current = self.current_account()
        selected_id = selected_id or (current.id if current is not None else None)
        self.accounts = self._load_accounts()
        self._refresh_account_menu(selected_id)
        self.current_folder = None
        self.current_summary = None
        self.current_message = None
        if self.accounts and refresh:
            self.refresh_folders()
        elif not self.accounts:
            self.folder_tree.delete(*self.folder_tree.get_children())
            self.message_tree.delete(*self.message_tree.get_children())
            self._set_status("请先添加邮件账户。")

    def select_account(self, identifier: str = "") -> MailAccount | None:
        account = resolve_mail_account(self.accounts, {"account": identifier})
        if account is None and not identifier:
            account = self.current_account()
        if account is None:
            return None
        label = next(
            (name for name, item in self.account_by_label.items() if item.id == account.id),
            "",
        )
        if label:
            self.account_var.set(label)
        return account

    def open_compose_from_ai(self, spec: dict) -> str:
        identifier = str(
            spec.get("account") or spec.get("account_id") or spec.get("email") or ""
        ).strip()
        account = self.select_account(identifier)
        if account is None:
            raise RuntimeError("未找到用于写信的邮件账户。")
        raw_attachments = spec.get("attachments") or spec.get("files") or []
        if isinstance(raw_attachments, (str, Path)):
            raw_attachments = [raw_attachments]
        attachments = [Path(str(value)) for value in raw_attachments if Path(str(value)).is_file()]
        self.open_compose(
            str(spec.get("to") or spec.get("recipient") or ""),
            str(spec.get("subject") or ""),
            str(spec.get("body") or spec.get("content") or spec.get("message") or ""),
            cc_value=str(spec.get("cc") or ""),
            attachments=attachments,
        )
        return f"已打开写邮件窗口，发件账户：{account.label}。"

    def _account_changed(self) -> None:
        self.current_folder = None
        self.current_message = None
        self.refresh_folders()

    def _password_for(self, account: MailAccount) -> str:
        return _mail_password(account)

    def _open_imap(self, account: MailAccount, password: str | None = None):
        return open_imap_account(account, password or "")

    def _open_smtp(self, account: MailAccount, password: str | None = None):
        return open_smtp_account(account, password or "")

    def _async(
        self,
        name: str,
        work: Callable,
        success: Callable | None = None,
        failure: Callable[[str], None] | None = None,
    ) -> None:
        def runner() -> None:
            try:
                result = work()
            except Exception as exc:
                message = str(exc) or exc.__class__.__name__
                if failure is None:
                    self._post(lambda message=message: self._set_status(f"{name}失败：{message}"))
                else:
                    self._post(lambda message=message: failure(message))
                return
            if success is not None:
                self._post(lambda result=result: success(result))

        threading.Thread(target=runner, daemon=True, name=f"Passer-Mail-{name}").start()

    def _post(self, callback: Callable) -> None:
        if self.closed:
            return
        try:
            self.window.after(0, callback)
        except (tk.TclError, RuntimeError):
            pass

    def _set_status(self, text: str) -> None:
        self.status_var.set(text)

    def refresh_folders(self) -> None:
        account = self.current_account()
        if account is None:
            self._set_status("请先添加邮件账户。")
            return
        self.load_generation += 1
        generation = self.load_generation
        self._set_status(f"正在连接 {account.label}…")

        def work() -> list[MailFolder]:
            client = self._open_imap(account)
            try:
                status, data = client.list()
                if status != "OK":
                    raise RuntimeError("无法读取邮箱文件夹。")
                folders: list[MailFolder] = []
                for raw in data or []:
                    parsed = parse_folder_line(raw)
                    if parsed is None:
                        continue
                    raw_name, display_name, flags = parsed
                    if "\\noselect" not in flags:
                        folders.append(MailFolder(raw_name, display_name, flags))
                folders.sort(key=self._folder_sort_key)
                return folders
            finally:
                try:
                    client.logout()
                except Exception:
                    pass

        def success(folders: list[MailFolder]) -> None:
            if generation != self.load_generation:
                return
            self.folders = folders
            self.folder_by_iid.clear()
            self.folder_tree.delete(*self.folder_tree.get_children())
            for index, folder in enumerate(folders):
                iid = f"folder-{index}"
                self.folder_by_iid[iid] = folder
                self.folder_tree.insert("", tk.END, iid=iid, text=self._folder_label(folder))
            if folders:
                inbox_iid = next((iid for iid, folder in self.folder_by_iid.items() if folder.raw_name.casefold() == "inbox"), next(iter(self.folder_by_iid)))
                self.folder_tree.selection_set(inbox_iid)
                self.folder_tree.focus(inbox_iid)
                self.current_folder = self.folder_by_iid[inbox_iid]
                self.refresh_messages()
            else:
                self._set_status("账户连接成功，但没有找到可选文件夹。")

        self._async("连接邮箱", work, success)

    @staticmethod
    def _folder_sort_key(folder: MailFolder) -> tuple[int, str]:
        flags = set(folder.flags)
        name = folder.raw_name.casefold()
        if name == "inbox" or "\\inbox" in flags:
            rank = 0
        elif "\\sent" in flags:
            rank = 1
        elif "\\drafts" in flags:
            rank = 2
        elif "\\junk" in flags:
            rank = 3
        elif "\\trash" in flags:
            rank = 4
        else:
            rank = 10
        return rank, folder.display_name.casefold()

    @staticmethod
    def _folder_label(folder: MailFolder) -> str:
        flags = set(folder.flags)
        if folder.raw_name.casefold() == "inbox" or "\\inbox" in flags:
            return "收件箱"
        if "\\sent" in flags:
            return "已发送"
        if "\\drafts" in flags:
            return "草稿"
        if "\\junk" in flags:
            return "垃圾邮件"
        if "\\trash" in flags:
            return "已删除"
        return folder.display_name

    def _folder_selected(self, _event=None) -> None:
        selected = self.folder_tree.selection()
        if not selected:
            return
        folder = self.folder_by_iid.get(selected[0])
        if folder is None:
            return
        self.search_var.set("")
        self.show_message_list()
        if folder == self.current_folder:
            self._render_messages()
            return
        self.current_folder = folder
        self.current_summary = None
        self.current_message = None
        self.refresh_messages()

    def _contact_selected(self, _event=None) -> None:
        selected = self.contact_tree.selection()
        if not selected:
            return
        query = self.contact_by_iid.get(selected[0], "")
        if query:
            self.search_var.set(query)
            self.show_message_list()

    def refresh_current(self) -> None:
        if self.current_folder is None:
            self.refresh_folders()
        else:
            self.refresh_messages()

    def refresh_messages(self) -> None:
        account = self.current_account()
        folder = self.current_folder
        if account is None or folder is None:
            return
        self.view_title_var.set(self._folder_label(folder))
        self.show_message_list()
        self.load_generation += 1
        generation = self.load_generation
        self._set_status(f"正在读取 {self._folder_label(folder)}…")

        def work() -> list[MailSummary]:
            client = self._open_imap(account)
            try:
                status, _ = client.select(quote_mailbox(folder.raw_name), readonly=True)
                if status != "OK":
                    raise RuntimeError(f"无法打开文件夹：{folder.display_name}")
                status, data = client.uid("search", None, "ALL")
                if status != "OK" or not data:
                    return []
                uids = data[0].split()[-MAX_MESSAGES:]
                if not uids:
                    return []
                sequence = b",".join(uids)
                status, fetched = client.uid(
                    "fetch",
                    sequence,
                    "(UID FLAGS RFC822.SIZE BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])",
                )
                if status != "OK":
                    raise RuntimeError("无法读取邮件列表。")
                by_uid: dict[str, MailSummary] = {}
                for item in fetched or []:
                    if not isinstance(item, tuple) or len(item) < 2:
                        continue
                    meta = item[0].decode("ascii", errors="ignore") if isinstance(item[0], bytes) else str(item[0])
                    uid_match = re.search(r"\bUID\s+(\d+)", meta, re.I)
                    if not uid_match:
                        continue
                    uid = uid_match.group(1)
                    header_bytes = item[1] if isinstance(item[1], bytes) else bytes(item[1] or b"")
                    header = BytesParser(policy=policy.default).parsebytes(header_bytes)
                    subject = decode_header_text(header.get("Subject")) or "（无主题）"
                    sender_value = decode_header_text(header.get("From"))
                    sender_name, sender_email = parseaddr(sender_value)
                    sender = sender_name or sender_email or sender_value or "未知发件人"
                    date_value = str(header.get("Date") or "")
                    timestamp = 0.0
                    date_text = date_value
                    try:
                        parsed_date = parsedate_to_datetime(date_value)
                        if parsed_date is not None:
                            timestamp = parsed_date.timestamp()
                            date_text = parsed_date.astimezone().strftime("%m-%d %H:%M")
                    except Exception:
                        pass
                    size_match = re.search(r"RFC822\.SIZE\s+(\d+)", meta, re.I)
                    unread = "\\Seen".casefold() not in meta.casefold()
                    flagged = "\\Flagged".casefold() in meta.casefold()
                    by_uid[uid] = MailSummary(
                        uid,
                        subject,
                        sender,
                        sender_email,
                        date_text,
                        timestamp,
                        unread,
                        size=int(size_match.group(1)) if size_match else 0,
                        flagged=flagged,
                    )
                return [by_uid[uid.decode("ascii")] for uid in reversed(uids) if uid.decode("ascii") in by_uid]
            finally:
                try:
                    client.logout()
                except Exception:
                    pass

        def success(summaries: list[MailSummary]) -> None:
            if generation != self.load_generation:
                return
            self.summaries = summaries
            self._render_messages()
            self._render_contacts()
            self._set_status(f"{self._folder_label(folder)}：{len(summaries)} 封邮件")

        self._async("读取邮件", work, success)

    def _render_messages(self) -> None:
        if not hasattr(self, "message_tree"):
            return
        query = self.search_var.get().strip().casefold()
        selected_uid = self.current_summary.uid if self.current_summary else ""
        self.summary_by_iid.clear()
        self.message_tree.delete(*self.message_tree.get_children())
        visible_count = 0
        for summary in self.summaries:
            haystack = f"{summary.subject}\n{summary.sender}\n{summary.sender_email}".casefold()
            if query and query not in haystack:
                continue
            visible_count += 1
            iid = f"mail-{summary.uid}"
            self.summary_by_iid[iid] = summary
            prefix = "★  " if summary.flagged else ("未读  " if summary.unread else "")
            title = prefix + summary.subject
            self.message_tree.insert("", tk.END, iid=iid, text=title, values=(summary.sender, summary.date_text))
            if summary.uid == selected_uid and self.reader_view.winfo_manager():
                self.message_tree.selection_set(iid)
        self.message_count_var.set(f"{visible_count} 封")
        self._update_navigation_buttons()

    def _render_contacts(self) -> None:
        contacts: dict[str, tuple[str, int]] = {}
        for summary in self.summaries:
            key = (summary.sender_email or summary.sender).casefold()
            if not key:
                continue
            label, count = contacts.get(key, (summary.sender or summary.sender_email, 0))
            contacts[key] = (label, count + 1)
        self.contact_by_iid.clear()
        self.contact_tree.delete(*self.contact_tree.get_children())
        for index, (key, (label, count)) in enumerate(sorted(contacts.items(), key=lambda item: item[1][0].casefold())):
            iid = f"contact-{index}"
            self.contact_by_iid[iid] = key
            suffix = f"  {count}" if count > 1 else ""
            self.contact_tree.insert("", tk.END, iid=iid, text=label + suffix)

    def show_message_list(self) -> None:
        if not hasattr(self, "list_view"):
            return
        self.reader_view.pack_forget()
        if not self.list_view.winfo_manager():
            self.list_view.pack(fill=tk.BOTH, expand=True)
        selected = self.message_tree.selection()
        if selected:
            self.message_tree.selection_remove(*selected)
        self.back_button.configure(state=tk.DISABLED)
        self.view_title_var.set(self._folder_label(self.current_folder) if self.current_folder else "邮件")
        self._update_navigation_buttons()

    def show_reader(self) -> None:
        self.list_view.pack_forget()
        if not self.reader_view.winfo_manager():
            self.reader_view.pack(fill=tk.BOTH, expand=True)
        self.back_button.configure(state=tk.NORMAL)
        self.view_title_var.set("阅读邮件")
        self._update_navigation_buttons()

    def _visible_summaries(self) -> list[MailSummary]:
        query = self.search_var.get().strip().casefold()
        if not query:
            return list(self.summaries)
        return [
            summary for summary in self.summaries
            if query in f"{summary.subject}\n{summary.sender}\n{summary.sender_email}".casefold()
        ]

    def _update_navigation_buttons(self) -> None:
        if not hasattr(self, "prev_button"):
            return
        visible = self._visible_summaries()
        current_uid = self.current_summary.uid if self.current_summary else ""
        index = next((position for position, item in enumerate(visible) if item.uid == current_uid), -1)
        reader_visible = bool(self.reader_view.winfo_manager())
        self.prev_button.configure(state=(tk.NORMAL if reader_visible and index > 0 else tk.DISABLED))
        self.next_button.configure(state=(tk.NORMAL if reader_visible and 0 <= index < len(visible) - 1 else tk.DISABLED))

    def navigate_message(self, offset: int) -> None:
        visible = self._visible_summaries()
        if not visible or self.current_summary is None:
            return
        index = next((position for position, item in enumerate(visible) if item.uid == self.current_summary.uid), -1)
        target = index + int(offset)
        if 0 <= target < len(visible):
            self.load_message(visible[target])

    def _message_selected(self, _event=None) -> None:
        selected = self.message_tree.selection()
        if not selected:
            return
        summary = self.summary_by_iid.get(selected[0])
        if summary is None:
            return
        if self.current_summary is not None and summary.uid == self.current_summary.uid and self.current_message is not None:
            self.show_reader()
            return
        self.load_message(summary)

    def load_message(self, summary: MailSummary) -> None:
        account = self.current_account()
        folder = self.current_folder
        if account is None or folder is None:
            return
        self.current_summary = summary
        self.current_message = None
        self.show_reader()
        self.subject_var.set(summary.subject)
        self.meta_var.set(f"发件人：{summary.sender} <{summary.sender_email}>    日期：{summary.date_text}")
        self._set_body("正在加载邮件正文…")

        def work() -> Message:
            client = self._open_imap(account)
            try:
                status, _ = client.select(quote_mailbox(folder.raw_name), readonly=False)
                if status != "OK":
                    raise RuntimeError("无法打开邮件文件夹。")
                status, data = client.uid("fetch", summary.uid, "(UID BODY.PEEK[])")
                if status != "OK":
                    raise RuntimeError("无法下载邮件正文。")
                raw_message = next((item[1] for item in data or [] if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], bytes)), None)
                if raw_message is None:
                    raise RuntimeError("邮件正文为空或格式不受支持。")
                client.uid("store", summary.uid, "+FLAGS.SILENT", "(\\Seen)")
                return BytesParser(policy=policy.default).parsebytes(raw_message)
            finally:
                try:
                    client.logout()
                except Exception:
                    pass

        def success(message: Message) -> None:
            if self.current_summary is None or self.current_summary.uid != summary.uid:
                return
            self.current_message = message
            summary.unread = False
            self._set_body(message_body_text(message))
            self._render_attachments(message)
            self._render_messages()
            self.show_reader()
            self._set_status("邮件已加载。")

        self._async("加载邮件", work, success)

    def _set_body(self, text: str) -> None:
        self.body_text.configure(state=tk.NORMAL)
        self.body_text.delete("1.0", tk.END)
        self.body_text.insert("1.0", text)
        self.body_text.configure(state=tk.DISABLED)

    def _render_attachments(self, message: Message | None) -> None:
        self.current_attachments = message_attachments(message) if message is not None else []
        self.attachment_list.delete(0, tk.END)
        for name, part in self.current_attachments:
            payload = part.get_payload(decode=True) or b""
            self.attachment_list.insert(tk.END, f"{name}  ({self._format_size(len(payload))})")
        if self.current_attachments:
            self.attachment_list.selection_set(0)

    @staticmethod
    def _format_size(value: int) -> str:
        size = float(value)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024
        return f"{value} B"

    def _selected_attachment(self) -> tuple[str, Message] | None:
        selection = self.attachment_list.curselection()
        if not selection or selection[0] >= len(self.current_attachments):
            self._set_status("请选择一个附件。")
            return None
        return self.current_attachments[selection[0]]

    def save_attachment(self) -> None:
        selected = self._selected_attachment()
        if selected is None:
            return
        name, part = selected
        target = filedialog.asksaveasfilename(title="保存附件", initialfile=Path(name).name, parent=self.window)
        if not target:
            return
        try:
            Path(target).write_bytes(part.get_payload(decode=True) or b"")
            self._set_status(f"附件已保存：{target}")
        except Exception as exc:
            self._set_status(f"保存附件失败：{exc}")

    def open_attachment(self) -> None:
        selected = self._selected_attachment()
        if selected is None:
            return
        name, part = selected
        safe_name = re.sub(r'[<>:"/\\|?*]+', "_", Path(name).name) or "attachment"
        target_dir = self.mail_dir / "Attachments"
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"{int(time.time())}-{safe_name}"
            target.write_bytes(part.get_payload(decode=True) or b"")
            os.startfile(target)
            self._set_status(f"已打开附件：{safe_name}")
        except Exception as exc:
            self._set_status(f"打开附件失败：{exc}")

    def mark_current_unread(self) -> None:
        self._set_seen(False)

    def toggle_current_flag(self) -> None:
        summary = self.current_summary
        if summary is not None:
            self._set_flagged(not summary.flagged)

    def _set_flagged(self, flagged: bool) -> None:
        account = self.current_account()
        folder = self.current_folder
        summary = self.current_summary
        if account is None or folder is None or summary is None:
            return

        def work() -> None:
            client = self._open_imap(account)
            try:
                client.select(quote_mailbox(folder.raw_name), readonly=False)
                operator = "+FLAGS.SILENT" if flagged else "-FLAGS.SILENT"
                status, _ = client.uid("store", summary.uid, operator, "(\\Flagged)")
                if status != "OK":
                    raise RuntimeError("服务器未接受标记操作。")
            finally:
                try:
                    client.logout()
                except Exception:
                    pass

        def success(_result=None) -> None:
            summary.flagged = flagged
            self._render_messages()
            self.show_reader()
            self._set_status("已标记为重要邮件。" if flagged else "已取消重要标记。")

        self._async("标记邮件", work, success)

    def _set_seen(self, seen: bool) -> None:
        account = self.current_account()
        folder = self.current_folder
        summary = self.current_summary
        if account is None or folder is None or summary is None:
            return

        def work() -> None:
            client = self._open_imap(account)
            try:
                client.select(quote_mailbox(folder.raw_name), readonly=False)
                operator = "+FLAGS.SILENT" if seen else "-FLAGS.SILENT"
                status, _ = client.uid("store", summary.uid, operator, "(\\Seen)")
                if status != "OK":
                    raise RuntimeError("服务器未接受状态修改。")
            finally:
                try:
                    client.logout()
                except Exception:
                    pass

        def success(_result=None) -> None:
            summary.unread = not seen
            self._render_messages()
            self._set_status("已标记为已读。" if seen else "已标记为未读。")

        self._async("修改邮件状态", work, success)

    def open_more_menu(self) -> None:
        menu = tk.Menu(self.window, tearoff=False, bg="#ffffff", fg="#1f2937",
                       activebackground=self.theme.accent_soft, activeforeground=self.theme.accent,
                       bd=1, relief=tk.SOLID, font=self._font(9))
        menu.add_command(label="标记为未读", command=self.mark_current_unread)
        menu.add_command(label="保存附件", command=self.save_attachment)
        menu.add_command(label="打开附件", command=self.open_attachment)
        menu.add_separator()
        menu.add_command(label="账户设置", command=self.open_account_dialog)
        try:
            menu.tk_popup(self.more_button.winfo_rootx(), self.more_button.winfo_rooty() - menu.winfo_reqheight())
        finally:
            menu.grab_release()

    def delete_current(self) -> None:
        account = self.current_account()
        folder = self.current_folder
        summary = self.current_summary
        if account is None or folder is None or summary is None:
            return
        if not messagebox.askyesno("删除邮件", f"确定删除“{summary.subject}”吗？", parent=self.window):
            return

        def work() -> None:
            client = self._open_imap(account)
            try:
                client.select(quote_mailbox(folder.raw_name), readonly=False)
                status, _ = client.uid("store", summary.uid, "+FLAGS.SILENT", "(\\Deleted)")
                if status != "OK":
                    raise RuntimeError("服务器未接受删除操作。")
                client.expunge()
            finally:
                try:
                    client.logout()
                except Exception:
                    pass

        def success(_result=None) -> None:
            self.current_summary = None
            self.current_message = None
            self.subject_var.set("选择一封邮件")
            self.meta_var.set("")
            self._set_body("")
            self._render_attachments(None)
            self.show_message_list()
            self.refresh_messages()

        self._async("删除邮件", work, success)

    def open_compose(
        self,
        to_value: str = "",
        subject: str = "",
        body: str = "",
        *,
        cc_value: str = "",
        attachments: list[Path] | None = None,
    ) -> None:
        account = self.current_account()
        if account is None:
            self.open_account_dialog()
            return
        window = ComposeWindow(
            self,
            account,
            to_value=to_value,
            cc_value=cc_value,
            subject=subject,
            body=body,
            attachments=attachments,
        )
        self.compose_windows.append(window)

    def reply_current(self) -> None:
        if self.current_summary is None or self.current_message is None:
            self._set_status("请先选择并加载一封邮件。")
            return
        subject = self.current_summary.subject
        if not subject.casefold().startswith("re:"):
            subject = "Re: " + subject
        original = message_body_text(self.current_message)
        body = f"\n\n----- 原邮件 -----\n发件人：{self.current_summary.sender} <{self.current_summary.sender_email}>\n日期：{self.current_summary.date_text}\n主题：{self.current_summary.subject}\n\n{original}"
        self.open_compose(self.current_summary.sender_email, subject, body)

    def forward_current(self) -> None:
        if self.current_summary is None or self.current_message is None:
            self._set_status("请先选择并加载一封邮件。")
            return
        subject = self.current_summary.subject
        if not subject.casefold().startswith("fwd:"):
            subject = "Fwd: " + subject
        original = message_body_text(self.current_message)
        body = f"\n\n----- 转发邮件 -----\n发件人：{self.current_summary.sender} <{self.current_summary.sender_email}>\n日期：{self.current_summary.date_text}\n主题：{self.current_summary.subject}\n\n{original}"
        self.open_compose("", subject, body)

    def send_message(
        self,
        account: MailAccount,
        to_value: str,
        cc_value: str,
        subject: str,
        body: str,
        attachments: list[Path],
        on_success: Callable,
        on_failure: Callable[[str], None],
    ) -> None:
        to_addresses = [address for _name, address in getaddresses([to_value.replace(";", ",")]) if address]
        cc_addresses = [address for _name, address in getaddresses([cc_value.replace(";", ",")]) if address]
        if not to_addresses:
            raise ValueError("请填写有效的收件人地址。")
        message = EmailMessage()
        message["From"] = formataddr((account.display_name, account.email)) if account.display_name else account.email
        message["To"] = ", ".join(to_addresses)
        if cc_addresses:
            message["Cc"] = ", ".join(cc_addresses)
        message["Subject"] = subject.strip() or "（无主题）"
        message.set_content(body)
        for path in attachments:
            mime_type, _encoding = mimetypes.guess_type(str(path))
            main_type, sub_type = (mime_type or "application/octet-stream").split("/", 1)
            message.add_attachment(path.read_bytes(), maintype=main_type, subtype=sub_type, filename=path.name)

        def work() -> None:
            client = self._open_smtp(account)
            try:
                client.send_message(message)
            finally:
                try:
                    client.quit()
                except Exception:
                    client.close()

        def success(_result=None) -> None:
            self._set_status(f"邮件已发送给：{', '.join(to_addresses)}")
            on_success()

        self._set_status("正在发送邮件…")
        self._async("发送邮件", work, success, on_failure)

    def open_account_dialog(self) -> None:
        AccountDialog(self, self.current_account())

    def save_account(self, account: MailAccount, password: str) -> None:
        existing = next((index for index, item in enumerate(self.accounts) if item.id == account.id), None)
        if existing is None:
            self.accounts.append(account)
        else:
            self.accounts[existing] = account
        if password:
            save_credential(account.id, account.username, password)
        self._save_accounts()
        self._refresh_account_menu(account.id)
        self._set_status(f"账户已保存：{account.label}")
        self.refresh_folders()

    def remove_account(self, account: MailAccount) -> None:
        self.accounts = [item for item in self.accounts if item.id != account.id]
        delete_credential(account.id)
        self._save_accounts()
        self._refresh_account_menu()
        self.folder_tree.delete(*self.folder_tree.get_children())
        self.message_tree.delete(*self.message_tree.get_children())
        self._set_status("邮件账户已删除。")
        if self.accounts:
            self.refresh_folders()

    def test_account(self, account: MailAccount, password: str, callback: Callable[[str], None]) -> None:
        def work() -> str:
            resolved_password = password or load_credential(account.id)
            if not resolved_password:
                raise RuntimeError("请输入授权码或应用密码。")
            imap = self._open_imap(account, resolved_password)
            try:
                imap.noop()
            finally:
                try:
                    imap.logout()
                except Exception:
                    pass
            smtp = self._open_smtp(account, resolved_password)
            try:
                smtp.noop()
            finally:
                try:
                    smtp.quit()
                except Exception:
                    smtp.close()
            return "IMAP 与 SMTP 连接均成功。"

        self._async("测试账户", work, callback, lambda message: callback("测试失败：" + message))

    def minimize(self) -> None:
        if self._minimize_in_progress:
            return
        self._minimize_in_progress = True
        if self._frame_restore_after_id is not None:
            try:
                self.window.after_cancel(self._frame_restore_after_id)
            except (tk.TclError, RuntimeError):
                pass
            self._frame_restore_after_id = None
        manager = getattr(self.app, "focus_manager", None)
        if manager is not None:
            manager.cancel_pending(self.window)
        minimize_frameless_window(self.window)

        def finish_transition() -> None:
            self._minimize_in_progress = False
            try:
                if self.window.state() == "normal":
                    self._restore_custom_frame()
            except (tk.TclError, RuntimeError):
                pass

        try:
            self.window.after(140, finish_transition)
        except (tk.TclError, RuntimeError):
            finish_transition()

    def _restore_custom_frame(self, event=None) -> None:
        if getattr(event, "widget", self.window) is not self.window:
            return
        if self._minimize_in_progress:
            return
        if self._frame_restore_after_id is not None:
            try:
                self.window.after_cancel(self._frame_restore_after_id)
            except (tk.TclError, RuntimeError):
                pass

        def restore() -> None:
            self._frame_restore_after_id = None
            if self._minimize_in_progress:
                return
            try:
                if self.window.state() == "normal":
                    self.window.overrideredirect(True)
            except (tk.TclError, RuntimeError):
                pass

        try:
            self._frame_restore_after_id = self.window.after(35, restore)
        except (tk.TclError, RuntimeError):
            pass

    def show(self) -> None:
        try:
            self.window.deiconify()
            self.window.lift()
            self.window.focus_force()
            self._restore_custom_frame()
        except tk.TclError:
            pass

    def start_move(self, event: tk.Event) -> None:
        self.move_start = (event.x_root, event.y_root, self.window.winfo_x(), self.window.winfo_y())

    def do_move(self, event: tk.Event) -> None:
        if not self.move_start:
            return
        sx, sy, wx, wy = self.move_start
        self.window.geometry(f"+{wx + event.x_root - sx}+{wy + event.y_root - sy}")

    def close(self) -> None:
        self.closed = True
        for compose in list(self.compose_windows):
            try:
                compose.close()
            except Exception:
                pass
        try:
            self.window.destroy()
        except tk.TclError:
            pass


class AccountDialog:
    WIDTH = 620
    HEIGHT = 650

    def __init__(self, owner: MailWindow, account: MailAccount | None):
        self.owner = owner
        self.account = account
        self.dialog = tk.Toplevel(owner.window)
        self.dialog.withdraw()
        self.dialog.overrideredirect(True)
        self.dialog.configure(bg=owner.theme.border)
        self.dialog.attributes("-topmost", owner.window.attributes("-topmost"))
        self.move_start = None
        self.vars = {
            "provider": tk.StringVar(value=account.provider if account else "QQ 邮箱"),
            "label": tk.StringVar(value=account.label if account else ""),
            "email": tk.StringVar(value=account.email if account else ""),
            "display_name": tk.StringVar(value=account.display_name if account else ""),
            "username": tk.StringVar(value=account.username if account else ""),
            "password": tk.StringVar(value=""),
            "imap_host": tk.StringVar(value=account.imap_host if account else ""),
            "imap_port": tk.StringVar(value=str(account.imap_port) if account else "993"),
            "imap_security": tk.StringVar(value=account.imap_security if account else "SSL/TLS"),
            "smtp_host": tk.StringVar(value=account.smtp_host if account else ""),
            "smtp_port": tk.StringVar(value=str(account.smtp_port) if account else "465"),
            "smtp_security": tk.StringVar(value=account.smtp_security if account else "SSL/TLS"),
            "status": tk.StringVar(value="密码将保存到 Windows 凭据管理器。" if WIN_CREDENTIALS_AVAILABLE else "当前环境无法安全保存密码。"),
        }
        self._build()
        if account is None:
            self._apply_provider()
        self.dialog.bind("<Escape>", lambda _event: self.close())
        x = owner.window.winfo_rootx() + max(0, (owner.window.winfo_width() - self.WIDTH) // 2)
        y = owner.window.winfo_rooty() + max(0, (owner.window.winfo_height() - self.HEIGHT) // 2)
        owner.theme.place_toplevel_absolute(self.dialog, self.WIDTH, self.HEIGHT, x, y)
        self.dialog.deiconify()
        self.dialog.focus_force()

    def _build(self) -> None:
        o = self.owner
        shell = tk.Frame(self.dialog, bg=o.theme.surface_bg, highlightthickness=1, highlightbackground=o.theme.border)
        shell.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        bar = tk.Frame(shell, bg=o.theme.title_bg, height=44)
        bar.pack(fill=tk.X)
        bar.pack_propagate(False)
        title = tk.Label(bar, text="邮件账户", bg=o.theme.title_bg, fg="#dbe7ff", anchor=tk.W, font=o._font(10, "bold"))
        title.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=14)
        o._chrome_button(bar, "×", self.close, close=True).pack(side=tk.RIGHT, padx=(0, 8), pady=7)
        for widget in (bar, title):
            widget.bind("<ButtonPress-1>", self._start_move)
            widget.bind("<B1-Motion>", self._do_move)

        body = tk.Frame(shell, bg=o.theme.surface_bg)
        body.pack(fill=tk.BOTH, expand=True, padx=20, pady=16)
        provider_row = tk.Frame(body, bg=o.theme.surface_bg)
        provider_row.pack(fill=tk.X, pady=(0, 7))
        tk.Label(provider_row, text="服务商", width=12, anchor=tk.W, bg=o.theme.surface_bg, fg="#111827", font=o._font(9, "bold")).pack(side=tk.LEFT)
        provider = o._select(
            provider_row,
            self.vars["provider"],
            tuple(PROVIDER_PRESETS),
            command=self._apply_provider,
            width=360,
            height=34,
        )
        provider.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self._row(body, "账户名称", "label")
        email_entry = self._row(body, "邮箱地址", "email")
        email_entry.bind("<FocusOut>", lambda _event: self._fill_identity())
        self._row(body, "发件人名称", "display_name")
        self._row(body, "登录用户名", "username")
        self._row(body, "授权码/密码", "password", show="●")
        self._section(body, "接收服务器（IMAP）")
        self._server_row(body, "imap")
        self._section(body, "发送服务器（SMTP）")
        self._server_row(body, "smtp")
        tk.Label(body, textvariable=self.vars["status"], bg=o.theme.surface_bg, fg=o.theme.muted_fg,
                 anchor=tk.W, justify=tk.LEFT, wraplength=560, font=o._font(8)).pack(fill=tk.X, pady=(10, 8))

        actions = tk.Frame(body, bg=o.theme.surface_bg)
        actions.pack(side=tk.BOTTOM, fill=tk.X)
        o._button(actions, "保存", self.save, primary=True).pack(side=tk.RIGHT)
        o._button(actions, "测试连接", self.test).pack(side=tk.RIGHT, padx=(0, 7))
        o._button(actions, "取消", self.close).pack(side=tk.RIGHT, padx=(0, 7))
        if self.account is not None:
            o._button(actions, "删除账户", self.delete, danger=True).pack(side=tk.LEFT)

    def _row(self, parent, label: str, key: str, show: str = "") -> tk.Entry:
        row = tk.Frame(parent, bg=self.owner.theme.surface_bg)
        row.pack(fill=tk.X, pady=4)
        tk.Label(row, text=label, width=12, anchor=tk.W, bg=self.owner.theme.surface_bg, fg="#111827",
                 font=self.owner._font(9, "bold")).pack(side=tk.LEFT)
        entry = self.owner._entry(row, self.vars[key], show=show)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=5)
        return entry

    def _section(self, parent, text: str) -> None:
        tk.Label(parent, text=text, bg=self.owner.theme.surface_bg, fg=self.owner.theme.accent,
                 anchor=tk.W, font=self.owner._font(9, "bold")).pack(fill=tk.X, pady=(12, 3))

    def _server_row(self, parent, prefix: str) -> None:
        row = tk.Frame(parent, bg=self.owner.theme.surface_bg)
        row.pack(fill=tk.X, pady=4)
        tk.Label(row, text="服务器", width=12, anchor=tk.W, bg=self.owner.theme.surface_bg, fg="#111827",
                 font=self.owner._font(9, "bold")).pack(side=tk.LEFT)
        self.owner._entry(row, self.vars[f"{prefix}_host"]).pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=5)
        tk.Label(row, text="端口", bg=self.owner.theme.surface_bg, fg="#475569", font=self.owner._font(8)).pack(side=tk.LEFT, padx=(8, 5))
        self.owner._entry(row, self.vars[f"{prefix}_port"], width=7).pack(side=tk.LEFT, ipady=5)
        security = self.owner._select(
            row,
            self.vars[f"{prefix}_security"],
            ("SSL/TLS", "STARTTLS", "无"),
            width=122,
            height=32,
            font_size=8,
        )
        security.pack(side=tk.LEFT, padx=(7, 0))

    def _apply_provider(self) -> None:
        preset = PROVIDER_PRESETS.get(self.vars["provider"].get(), {})
        for key, value in preset.items():
            self.vars[key].set(str(value))
        self._fill_identity()

    def _fill_identity(self) -> None:
        email_value = self.vars["email"].get().strip()
        if email_value and not self.vars["username"].get().strip():
            self.vars["username"].set(email_value)
        if email_value and not self.vars["label"].get().strip():
            self.vars["label"].set(email_value)

    def _build_account(self) -> MailAccount:
        self._fill_identity()
        email_value = self.vars["email"].get().strip()
        if "@" not in email_value:
            raise ValueError("请输入有效的邮箱地址。")
        imap_host = self.vars["imap_host"].get().strip()
        smtp_host = self.vars["smtp_host"].get().strip()
        if not imap_host or not smtp_host:
            raise ValueError("请填写 IMAP 和 SMTP 服务器。")
        try:
            imap_port = int(self.vars["imap_port"].get())
            smtp_port = int(self.vars["smtp_port"].get())
        except ValueError as exc:
            raise ValueError("服务器端口必须是数字。") from exc
        if not (1 <= imap_port <= 65535 and 1 <= smtp_port <= 65535):
            raise ValueError("服务器端口范围应为 1–65535。")
        return MailAccount(
            id=self.account.id if self.account else uuid.uuid4().hex,
            label=self.vars["label"].get().strip() or email_value,
            email=email_value,
            display_name=self.vars["display_name"].get().strip(),
            username=self.vars["username"].get().strip() or email_value,
            provider=self.vars["provider"].get(),
            imap_host=imap_host,
            imap_port=imap_port,
            imap_security=self.vars["imap_security"].get(),
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            smtp_security=self.vars["smtp_security"].get(),
        )

    def save(self) -> None:
        try:
            account = self._build_account()
            password = self.vars["password"].get()
            if self.account is None and not password:
                raise ValueError("首次添加账户时请输入授权码或应用密码。")
            self.owner.save_account(account, password)
        except Exception as exc:
            self.vars["status"].set(str(exc))
            return
        self.close()

    def test(self) -> None:
        try:
            account = self._build_account()
        except Exception as exc:
            self.vars["status"].set(str(exc))
            return
        self.vars["status"].set("正在测试 IMAP 与 SMTP…")
        self.owner.test_account(account, self.vars["password"].get(), self.vars["status"].set)

    def delete(self) -> None:
        if self.account is None:
            return
        if messagebox.askyesno("删除账户", f"确定删除邮件账户“{self.account.label}”吗？", parent=self.dialog):
            self.owner.remove_account(self.account)
            self.close()

    def _start_move(self, event: tk.Event) -> None:
        self.move_start = (event.x_root, event.y_root, self.dialog.winfo_x(), self.dialog.winfo_y())

    def _do_move(self, event: tk.Event) -> None:
        if self.move_start:
            sx, sy, wx, wy = self.move_start
            self.dialog.geometry(f"+{wx + event.x_root - sx}+{wy + event.y_root - sy}")

    def close(self) -> None:
        try:
            self.dialog.destroy()
        except tk.TclError:
            pass


class ComposeWindow:
    WIDTH = 720
    HEIGHT = 640

    def __init__(
        self,
        owner: MailWindow,
        account: MailAccount,
        to_value: str = "",
        cc_value: str = "",
        subject: str = "",
        body: str = "",
        attachments: list[Path] | None = None,
    ):
        self.owner = owner
        self.account = account
        self.attachments: list[Path] = [path for path in attachments or [] if path.is_file()]
        self.move_start = None
        self.to_var = tk.StringVar(value=to_value)
        self.cc_var = tk.StringVar(value=cc_value)
        self.subject_var = tk.StringVar(value=subject)
        self.status_var = tk.StringVar(value=f"发件账户：{account.label}")
        self.window = tk.Toplevel(owner.window)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.configure(bg=owner.theme.border)
        self.window.attributes("-topmost", owner.window.attributes("-topmost"))
        self._build(body)
        self.window.bind("<Escape>", lambda _event: self.close())
        x = owner.window.winfo_rootx() + max(0, (owner.window.winfo_width() - self.WIDTH) // 2)
        y = owner.window.winfo_rooty() + max(0, (owner.window.winfo_height() - self.HEIGHT) // 2)
        owner.theme.place_toplevel_absolute(self.window, self.WIDTH, self.HEIGHT, x, y)
        self.window.deiconify()
        self.window.focus_force()

    def _build(self, body_value: str) -> None:
        o = self.owner
        shell = tk.Frame(self.window, bg=o.theme.surface_bg, highlightthickness=1, highlightbackground=o.theme.border)
        shell.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        bar = tk.Frame(shell, bg=o.theme.title_bg, height=44)
        bar.pack(fill=tk.X)
        bar.pack_propagate(False)
        title = tk.Label(bar, text="写邮件", bg=o.theme.title_bg, fg="#dbe7ff", anchor=tk.W, font=o._font(10, "bold"))
        title.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=14)
        o._chrome_button(bar, "×", self.close, close=True).pack(side=tk.RIGHT, padx=(0, 8), pady=7)
        for widget in (bar, title):
            widget.bind("<ButtonPress-1>", self._start_move)
            widget.bind("<B1-Motion>", self._do_move)

        body = tk.Frame(shell, bg=o.theme.surface_bg)
        body.pack(fill=tk.BOTH, expand=True, padx=18, pady=14)
        self._field(body, "收件人", self.to_var)
        self._field(body, "抄送", self.cc_var)
        self._field(body, "主题", self.subject_var)
        editor_wrap = tk.Frame(body, bg="#ffffff", highlightthickness=1, highlightbackground=o.theme.border)
        editor_wrap.pack(fill=tk.BOTH, expand=True, pady=(7, 8))
        self.editor = tk.Text(editor_wrap, bd=0, relief=tk.FLAT, bg="#ffffff", fg="#1f2937", insertbackground=o.theme.accent,
                              wrap=tk.WORD, padx=10, pady=10, undo=True, font=o._font(9))
        self.editor.pack(fill=tk.BOTH, expand=True)
        bind_wheel_scroll(self.editor)
        self.editor.insert("1.0", body_value)
        attach_row = tk.Frame(body, bg=o.theme.surface_bg, height=48)
        attach_row.pack(fill=tk.X, pady=(0, 7))
        attach_row.pack_propagate(False)
        self.attachment_list = tk.Listbox(attach_row, height=2, bd=0, relief=tk.FLAT, bg="#f8fafc", fg="#334155",
                                          highlightthickness=1, highlightbackground=o.theme.border, font=o._font(8), exportselection=False)
        self.attachment_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._render_attachments()
        o._button(attach_row, "添加附件", self.add_attachments).pack(side=tk.LEFT, padx=(8, 0))
        o._button(attach_row, "移除", self.remove_attachment).pack(side=tk.LEFT, padx=(6, 0))
        footer = tk.Frame(body, bg=o.theme.surface_bg)
        footer.pack(fill=tk.X)
        tk.Label(footer, textvariable=self.status_var, bg=o.theme.surface_bg, fg=o.theme.muted_fg,
                 anchor=tk.W, font=o._font(8)).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.send_button = o._button(footer, "发送", self.send, primary=True)
        self.send_button.pack(side=tk.RIGHT)

    def _field(self, parent, label: str, variable: tk.StringVar) -> None:
        row = tk.Frame(parent, bg=self.owner.theme.surface_bg)
        row.pack(fill=tk.X, pady=4)
        tk.Label(row, text=label, width=8, anchor=tk.W, bg=self.owner.theme.surface_bg, fg="#111827",
                 font=self.owner._font(9, "bold")).pack(side=tk.LEFT)
        self.owner._entry(row, variable).pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=5)

    def add_attachments(self) -> None:
        selected = filedialog.askopenfilenames(title="选择附件", parent=self.window)
        for value in selected:
            path = Path(value)
            if path.is_file() and path not in self.attachments:
                self.attachments.append(path)
        self._render_attachments()

    def remove_attachment(self) -> None:
        selected = self.attachment_list.curselection()
        if selected:
            self.attachments.pop(selected[0])
            self._render_attachments()

    def _render_attachments(self) -> None:
        self.attachment_list.delete(0, tk.END)
        for path in self.attachments:
            self.attachment_list.insert(tk.END, f"{path.name}  ({self.owner._format_size(path.stat().st_size)})")

    def send(self) -> None:
        try:
            self.send_button.configure(state=tk.DISABLED)
            self.status_var.set("正在发送…")
            self.owner.send_message(
                self.account,
                self.to_var.get(),
                self.cc_var.get(),
                self.subject_var.get(),
                self.editor.get("1.0", "end-1c"),
                list(self.attachments),
                self.close,
                self._send_failed,
            )
        except Exception as exc:
            self.status_var.set(str(exc))
            self.send_button.configure(state=tk.NORMAL)

    def _send_failed(self, message: str) -> None:
        self.status_var.set("发送失败：" + message)
        self.send_button.configure(state=tk.NORMAL)
        self.owner._set_status("发送邮件失败：" + message)

    def _start_move(self, event: tk.Event) -> None:
        self.move_start = (event.x_root, event.y_root, self.window.winfo_x(), self.window.winfo_y())

    def _do_move(self, event: tk.Event) -> None:
        if self.move_start:
            sx, sy, wx, wy = self.move_start
            self.window.geometry(f"+{wx + event.x_root - sx}+{wy + event.y_root - sy}")

    def close(self) -> None:
        if self in self.owner.compose_windows:
            self.owner.compose_windows.remove(self)
        try:
            self.window.destroy()
        except tk.TclError:
            pass
