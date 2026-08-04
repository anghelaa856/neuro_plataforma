"""Tokens de diseño — paleta médica de confianza."""

COLORS = {
    "bg": "#F1F5F9",
    "surface": "#FFFFFF",
    "ink": "#0F172A",
    "muted": "#64748B",
    "line": "#E2E8F0",
    "brand": "#1D4ED8",
    "brand_soft": "#EFF6FF",
    "brand_mid": "#DBEAFE",
    "success": "#15803D",
    "success_bg": "#F0FDF4",
    "success_border": "#86EFAC",
    "danger": "#B91C1C",
    "danger_bg": "#FEF2F2",
    "danger_border": "#FCA5A5",
    "warning": "#B45309",
    "warning_bg": "#FFFBEB",
    "warning_border": "#FCD34D",
    "slate": "#334155",
}

PAGE_BG = (
    "radial-gradient(ellipse 120% 80% at 50% -20%, #DBEAFE 0%, transparent 55%),"
    "linear-gradient(180deg, #EFF6FF 0%, #F8FAFC 38%, #F1F5F9 100%)"
)

SURFACE_CARD = {
    "background": COLORS["surface"],
    "border": f"1px solid {COLORS['line']}",
    "border_radius": "20px",
    "box_shadow": "0 10px 30px rgba(15, 23, 42, 0.06)",
}

OPTION_BASE = {
    "width": "100%",
    "justify_content": "flex-start",
    "text_align": "left",
    "padding": "0.95rem 1rem",
    "border_radius": "14px",
    "min_height": "3.4rem",
    "height": "auto",
    "white_space": "normal",
    "font_weight": "500",
    "transition": "transform 0.18s ease, box-shadow 0.18s ease, background 0.18s ease, border-color 0.18s ease",
    "cursor": "pointer",
    "_hover": {
        "transform": "translateY(-1px)",
        "box_shadow": "0 6px 16px rgba(29, 78, 216, 0.12)",
    },
    "_active": {
        "transform": "translateY(0px) scale(0.99)",
    },
}
