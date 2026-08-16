"""
report_pdf.py
-------------
Builds a downloadable forensic PDF report for one analysis job:
source info, summary counters, and every logged event (timestamp,
type, confidence, location, evidence thumbnail).

Requires: pip install reportlab
"""

from __future__ import annotations

import os
from typing import Any, Dict

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    Image as RLImage,
    HRFlowable,
)

from config import Config

EVENT_TITLES = {
    "weapon": "WEAPON",
    "plate": "NUMBER PLATE",
    "anomaly": "ANOMALY",
    "violence": "VIOLENCE",
    "face": "PERSON OF INTEREST",
}

EVENT_HEX_COLORS = {
    "weapon": "#9B3B2E",
    "plate": "#2E6B9B",
    "anomaly": "#9B7A2E",
    "violence": "#9B3B2E",
    "face": "#C46A1A",
}


def _reports_dir() -> str:
    path = os.path.join(Config.SNAPSHOT_DIR, "_reports")
    os.makedirs(path, exist_ok=True)
    return path


def _snapshot_path_from_url(snapshot_url: str) -> str:
    """
    snapshot_url looks like /snapshots/{job_id}/{event_id}.jpg — map that
    back to the real file on disk under Config.SNAPSHOT_DIR.
    """
    if "/evidence/snapshots/" in snapshot_url:
        rel = snapshot_url.split("/evidence/snapshots/", 1)[-1]
    else:
        rel = snapshot_url.split("/snapshots/", 1)[-1]
    return os.path.join(Config.SNAPSHOT_DIR, rel)


def build_report_pdf(job: Dict[str, Any]) -> str:
    """
    Build the PDF for a job dict (as returned by analysis_pipeline.get_job)
    and return the path to the generated file.
    """

    job_id = job.get("job_id", "unknown")
    out_path = os.path.join(_reports_dir(), f"{job_id}.pdf")

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ReportTitle", parent=styles["Title"], fontSize=18, spaceAfter=4,
    )
    meta_style = ParagraphStyle(
        "Meta", parent=styles["Normal"], fontSize=9, textColor=colors.HexColor("#555555"),
    )
    section_style = ParagraphStyle(
        "Section", parent=styles["Heading2"], fontSize=12, spaceBefore=14, spaceAfter=6,
    )
    event_label_style = ParagraphStyle(
        "EventLabel", parent=styles["Normal"], fontSize=10, leading=13,
    )
    event_meta_style = ParagraphStyle(
        "EventMeta", parent=styles["Normal"], fontSize=8, textColor=colors.HexColor("#666666"), leading=11,
    )

    doc = SimpleDocTemplate(
        out_path,
        pagesize=A4,
        topMargin=18 * mm,
        bottomMargin=16 * mm,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        title=f"N.E.T.R.A Analysis Report — {job.get('original_name') or job_id}",
    )

    story = []

    # ---- Header ----
    story.append(Paragraph("N.E.T.R.A Surveillance Report", title_style))
    story.append(Paragraph(
        f"Source file: {job.get('original_name') or job.get('local_path') or '—'}",
        meta_style,
    ))
    story.append(Paragraph(f"Job ID: {job_id}", meta_style))
    story.append(Paragraph(f"Status: {job.get('status', '—')}", meta_style))
    story.append(Paragraph(f"Queued: {job.get('queued_at', '—')}   ·   Completed: {job.get('completed_at', '—')}", meta_style))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", color=colors.HexColor("#DDDDDD")))

    # ---- Summary ----
    summary = job.get("result") or {}
    events = sorted(job.get("events", []), key=lambda e: e.get("video_time_seconds", 0))

    story.append(Paragraph("Summary", section_style))
    summary_rows = [
        ["Frames analysed", str(summary.get("frames_processed", "—"))],
        ["Processing time (s)", str(summary.get("processing_seconds", "—"))],
        ["Total events logged", str(len(events))],
        ["Weapon events", str(sum(1 for e in events if e.get("type") == "weapon"))],
        ["Plate events", str(sum(1 for e in events if e.get("type") == "plate"))],
        ["Face / POI events", str(sum(1 for e in events if e.get("type") == "face"))],
        ["Anomaly events", str(sum(1 for e in events if e.get("type") == "anomaly"))],
        ["Violence events", str(sum(1 for e in events if e.get("type") == "violence"))],
    ]
    summary_table = Table(summary_rows, colWidths=[70 * mm, 90 * mm])
    summary_table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#555555")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, colors.HexColor("#EEEEEE")),
    ]))
    story.append(summary_table)

    # ---- Event log ----
    story.append(Paragraph("Event Timeline", section_style))

    if not events:
        story.append(Paragraph("No events were detected in this video.", styles["Normal"]))
    else:
        for ev in events:
            ev_type = ev.get("type", "event")
            title = EVENT_TITLES.get(ev_type, ev_type.upper())
            color_hex = EVENT_HEX_COLORS.get(ev_type, "#444444")

            thumb = None
            snapshot_url = ev.get("snapshot_url")
            if snapshot_url:
                snap_path = _snapshot_path_from_url(snapshot_url)
                if os.path.isfile(snap_path):
                    try:
                        thumb = RLImage(snap_path, width=42 * mm, height=28 * mm)
                    except Exception:
                        thumb = None

            label_para = Paragraph(
                f'<font color="{color_hex}"><b>{title}</b></font> '
                f'&nbsp;&nbsp;<b>{ev.get("video_timestamp", "—")}</b>   '
                f'(frame #{ev.get("frame_number", "—")})<br/>'
                f'{ev.get("label", "")}'
                + (f' · {ev.get("confidence") * 100:.1f}% confidence' if ev.get("confidence") else ''),
                event_label_style,
            )
            meta_para = Paragraph(
                f'Location: {ev.get("location", "—")}<br/>'
                f'Logged: {ev.get("wall_clock_time", "—")}',
                event_meta_style,
            )

            row = [thumb if thumb else "", [label_para, Spacer(1, 3), meta_para]]
            ev_table = Table([row], colWidths=[46 * mm, 114 * mm])
            ev_table.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#EEEEEE")),
            ]))
            story.append(ev_table)

    doc.build(story)
    return out_path