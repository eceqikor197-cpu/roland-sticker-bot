"""Модуль генерации HTML-страниц веб-админки Roland VP-540.

Загружает HTML-шаблоны из templates/*.html и экранирует пользовательские данные
с использованием html.escape.
"""

from __future__ import annotations

import datetime
import html
from pathlib import Path
from typing import Any, Dict

from storage import (
    format_user_trigger as storage_format_user_trigger,
    get_all_users as storage_get_all_users,
    list_all_jobs as storage_list_all_jobs,
)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

PERF_MODE_LABELS = {
    "none": "Только CutContour (надсечка)",
    "sheet_frame": "CutContour + Рамка листа (PerfCut)",
    "die_cut": "CutContour + Вырубка наклеек (PerfCut)",
}


def format_ts(ts: float) -> str:
    """Форматирует timestamp в строку вида DD.MM HH:MM (UTC+3, Москва)."""
    if not ts:
        return "—"
    dt = datetime.datetime.fromtimestamp(
        ts, tz=datetime.timezone(datetime.timedelta(hours=3))
    )
    return dt.strftime("%d.%m %H:%M")


def render_template(template_name: str, context: Dict[str, Any]) -> str:
    """Загружает шаблон из templates/<template_name> и подставляет переменные {{var}}."""
    tpl_path = TEMPLATES_DIR / template_name
    if not tpl_path.is_file():
        raise FileNotFoundError(f"Шаблон {template_name} не найден по пути {tpl_path}")
    content = tpl_path.read_text(encoding="utf-8")
    for key, val in context.items():
        placeholder = "{{" + key + "}}"
        content = content.replace(placeholder, str(val))
    return content


def render_login_page(
    next_url: str = "/",
    error_msg: str = "",
) -> str:
    """Формирует HTML-страницу входа администратора."""
    err_html = (
        f'<div style="background:#7f1d1d;color:#fecaca;padding:10px 14px;border-radius:8px;margin-bottom:14px;font-size:13px;">{html.escape(error_msg)}</div>'
        if error_msg
        else ""
    )
    return render_template(
        "login.html",
        {
            "err_html": err_html,
            "next_url": html.escape(next_url),
        },
    )


def render_dashboard(
    filter_uid_str: str,
    hot_msg: str = "",
    csrf_token: str = "",
    hot_queue_dir: Path | None = None,
) -> str:
    """Формирует главную страницу дашборда администратора."""
    users = storage_get_all_users()
    filter_uid = (
        int(filter_uid_str)
        if filter_uid_str != "all" and filter_uid_str.isdigit()
        else None
    )
    all_jobs = storage_list_all_jobs(filter_user_id=filter_uid)
    total_jobs_all = len(storage_list_all_jobs())
    total_contours = sum(int(j.get("num_contours", 1)) for j in all_jobs)

    if hot_queue_dir is not None and hot_queue_dir.exists():
        hot_files = list(hot_queue_dir.glob("*.eps"))
    else:
        hot_files = []

    user_pills = [
        f'<a class="pill {"active" if filter_uid is None else ""}" href="/?user=all">Все работы ({total_jobs_all})</a>'
    ]
    for u in sorted(
        users.values(), key=lambda x: float(x.get("last_seen", 0)), reverse=True
    ):
        uid = int(u["user_id"])
        fn = html.escape(
            f"{u.get('first_name', '')} {u.get('last_name', '')}".strip() or str(uid)
        )
        un = f" (@{html.escape(u['username'])})" if u.get("username") else ""
        cnt = len(storage_list_all_jobs(filter_user_id=uid))
        active_cls = "active" if filter_uid == uid else ""
        user_pills.append(
            f'<a class="pill {active_cls}" href="/?user={uid}">👤 {fn}{un} ({cnt})</a>'
        )

    cards_html = []
    for j in all_jobs:
        jid = html.escape(str(j["job_id"]))
        uid = int(j.get("user_id", 0))
        uinfo = users.get(str(uid), {"user_id": uid, "first_name": f"User {uid}"})
        uname_html = storage_format_user_trigger(uinfo)
        ts_str = format_ts(float(j.get("updated_at", j.get("created_at", 0))))
        nc = int(j.get("num_contours", 1))
        off = float(j.get("offset_mm", 2.5))
        w_mm = float(j.get("width_mm", 100))
        h_mm = float(j.get("height_mm", 100))
        qty = int(j.get("nesting_qty", 1))
        perf_mode = str(j.get("perf_cut_mode", "none"))
        size_mb = float(j.get("eps_size_mb", 0.0))
        calc = j.get("pricing", {})
        in_hot = bool(j.get("in_hot_folder"))
        last_act = html.escape(str(j.get("last_action", "Конвертация")))

        card_context = {
            "jid": jid,
            "uname_html": uname_html,
            "ts_str": ts_str,
            "last_act": last_act,
            "nc": nc,
            "qty": qty,
            "w_mm": f"{w_mm:g}",
            "h_mm": f"{h_mm:g}",
            "size_mb": f"{size_mb:.1f}",
            "off": f"{off:g}",
            "perf_mode_label": html.escape(
                PERF_MODE_LABELS.get(perf_mode, perf_mode)
            ),
            "cost_rub": calc.get("cost_rub", 30),
            "retail_rub": calc.get("retail_rub", 200),
            "csrf_token": html.escape(csrf_token),
            "hot_btn_text": "✅ В Hot Folder" if in_hot else "🚀 В Hot Folder",
        }
        cards_html.append(render_template("card.html", card_context))

    toast_html = (
        f'<div style="background:#065f46;color:#fff;padding:12px 18px;border-radius:10px;margin-bottom:18px;font-weight:600;">🚀 Файл <code>{html.escape(hot_msg)}</code> скопирован в сетевую папку плоттера <code>\\\\192.168.0.108\\Roland_HotFolder\\Queue_EPS\\</code>!</div>'
        if hot_msg
        else ""
    )

    dashboard_context = {
        "csrf_token": html.escape(csrf_token),
        "toast_html": toast_html,
        "user_count": len(users),
        "jobs_count": len(all_jobs),
        "total_contours": total_contours,
        "hot_files_count": len(hot_files),
        "user_pills": "".join(user_pills),
        "cards_html": (
            "".join(cards_html)
            if cards_html
            else '<p style="color:var(--muted);">Пока нет сконвертированных файлов.</p>'
        ),
    }
    return render_template("dashboard.html", dashboard_context)
