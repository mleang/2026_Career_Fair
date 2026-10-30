# ui_render.py
import base64
import html
import streamlit as st
import pathlib
from pathlib import Path

from audit_logger import log_auth_event


def load_css(path: str = "assets/styles.css"):
    p = pathlib.Path(path)
    if p.exists():
        st.markdown(f"<style>{p.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)
    else:
        # styles par défaut si assets/styles.css n'existe pas
        st.markdown("""
        <style>
        .main .block-container{max-width:1100px;padding-top:1rem;padding-bottom:.5rem;}
        h1.app-title{
          font-size:2.2rem; line-height:1.15; margin:0;
          background: linear-gradient(90deg,#111827,#2563eb,#111827);
          -webkit-background-clip: text; background-clip: text; color: transparent;
        }
        p.app-subtitle{color:#6b7280;margin:.25rem 0 .5rem;font-size:1rem}
        .hr-soft{height:1px;border:0;background:linear-gradient(90deg,transparent,#e5e7eb,transparent);margin:.8rem 0 1rem;}
        .logo img{border-radius:12px}
        .badge{display:inline-block;font-size:.75rem;font-weight:600;padding:.25rem .5rem;border-radius:999px;background:#eef2ff;color:#3730a3;border:1px solid #e0e7ff;margin-top:.25rem;}
        </style>
        """, unsafe_allow_html=True)

# def render_header(
#     title: str = "Career Fair",
#     subtitle: str = "Filtrer les candidats • Télécharger les CV • Suivre les décisions",
#     logo_path: str | None = None,
#     logo_width: int = 130,
#     badge_text: str | None = None,     # <- ajouté
# ):
#     col1, col2 = st.columns([1.6, 5])
#     with col1:
#         if logo_path and pathlib.Path(logo_path).exists():
#             st.image(logo_path, width=logo_width)
#         else:
#             st.markdown("### 🎓")
#     with col2:
#         st.markdown(f'<h1 class="app-title">{title}</h1>', unsafe_allow_html=True)
#         if subtitle:
#             st.markdown(f'<p class="app-subtitle">{subtitle}</p>', unsafe_allow_html=True)
#         if badge_text:
#             st.markdown(f'<span class="badge">{badge_text}</span>', unsafe_allow_html=True)
#     st.markdown('<div class="hr-soft"></div>', unsafe_allow_html=True)


# =====================================
# -------------- UI BOOT --------------
# =====================================
def configure_page(app_title: str, page_icon: str) -> None:
    """Configure the Streamlit page and load custom CSS."""

    st.set_page_config(
        page_title=app_title,
        page_icon=page_icon,
        layout="wide",
    )

    css_path = pathlib.Path("assets/styles.css")

    if css_path.exists():
        st.markdown(
            f"<style>{css_path.read_text(encoding='utf-8')}</style>",
            unsafe_allow_html=True,
        )

# ======================================
# ------------- HEADER -----------------
# ======================================
def render_header(logo_path: str, sponsor_name: str, username: str, recruiter_id: str, auth_log_path: str) -> None:
    logo_col, title_col, account_col = st.columns(
        [1.3, 5.1, 1.8],
        vertical_alignment="center",
    )

    with logo_col:
        try:
            # On vérifie si c'est un SVG
            if logo_path.lower().endswith('.svg'):
                # 1. Lire le fichier SVG local
                svg_string = Path(logo_path).read_text(encoding="utf-8")

                # 2. L'encoder en Base64 pour contourner les restrictions
                b64 = base64.b64encode(svg_string.encode("utf-8")).decode(
                    "utf-8"
                )

                # 3. L'injecter de manière sécurisée avec une taille fixée à 140px
                html_code = f"""
                    <div style="display: flex; justify-content: center; align-items: center; width: 140px;">
                        <img src="data:image/svg+xml;base64,{b64}" style="width: 140px; height: auto;" />
                    </div>
                """
                st.markdown(html_code, unsafe_allow_html=True)
            else:
                # Sécurité pour les logos PNG / JPG classiques
                st.image(logo_path, width=140)
        except Exception:
            pass

    with title_col:
        st.markdown(
            f"""
            <div class="app-header">
                <div class="app-title">{sponsor_name} Career Fair</div>
                <div class="app-subtitle">
                    Find candidates, review CVs and track outreach
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with account_col:
        st.markdown(
            f"""
            <div class="account-box">
                <div class="account-label"></div>
                <div class="account-user"
                    title="{html.escape(username, quote=True)}">
                    Welcome {html.escape(sponsor_name)}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # Center the smaller logout button
        _, logout_col, _ = st.columns([1, 2, 1])

        with logout_col:
            if st.button(
                "↪ Log out",
                key="logout_header",
                use_container_width=True,
            ):
                log_auth_event(
                    auth_log_path,
                    username=username,
                    event="logout",
                    success=True,
                )

                for key in ("authenticated", "username", "recruiter_id"):
                    st.session_state.pop(key, None)

                st.rerun()

    st.divider()
