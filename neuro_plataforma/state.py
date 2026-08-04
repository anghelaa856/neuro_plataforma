"""Estados Reflex — Auth, navegación, estudio, ingesta y dashboard."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import reflex as rx

from neuro_plataforma import services_bridge as sb
from neuro_plataforma.data import QUESTIONS as LOCAL_QUESTIONS
from neuro_plataforma.scoring import evaluar_bloque, shuffle_bloque


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class AuthState(rx.State):
    """Login / registro (UserRepository + Neon)."""

    is_authenticated: bool = False
    usuario_id: int = 0
    email: str = ""
    nombre: str = ""

    auth_mode: str = "login"  # login | register
    form_email: str = ""
    form_password: str = ""
    form_nombre: str = ""
    auth_error: str = ""
    auth_info: str = ""
    boot_message: str = ""
    loading: bool = False

    @rx.event
    def on_load(self):
        ok = sb.bootstrap_database()
        if not ok:
            self.boot_message = (
                "Sin conexión a Neon: "
                f"{sb.get_boot_error() or 'DATABASE_URL inválida'}. "
                "Puedes usar el modo demo local en Práctica."
            )
        else:
            self.boot_message = ""

    @rx.event
    def set_mode_login(self):
        self.auth_mode = "login"
        self.auth_error = ""
        self.auth_info = ""

    @rx.event
    def set_mode_register(self):
        self.auth_mode = "register"
        self.auth_error = ""
        self.auth_info = ""

    @rx.event
    def set_form_email(self, v: str):
        self.form_email = v

    @rx.event
    def set_form_password(self, v: str):
        self.form_password = v

    @rx.event
    def set_form_nombre(self, v: str):
        self.form_nombre = v

    @rx.event
    async def login(self):
        self.loading = True
        self.auth_error = ""
        self.auth_info = ""
        yield
        try:
            if not sb.bootstrap_database():
                self.auth_error = (
                    "No hay base de datos. Revisa DATABASE_URL en .env."
                )
                return
            user = sb.user_repo().authenticate(
                email=self.form_email, password=self.form_password
            )
            if not user:
                self.auth_error = "Email o contraseña incorrectos."
                return
            self.usuario_id = int(user["id_usuario"])
            self.email = str(user.get("email") or "")
            self.nombre = str(user.get("nombre") or "")
            self.is_authenticated = True
            self.form_password = ""
            app = await self.get_state(AppState)
            app.tab = "practica"
            study = await self.get_state(StudyState)
            study.reset_all()
        except Exception as exc:
            self.auth_error = f"Error al iniciar sesión: {exc}"
        finally:
            self.loading = False

    @rx.event
    async def register(self):
        self.loading = True
        self.auth_error = ""
        self.auth_info = ""
        yield
        try:
            if not sb.bootstrap_database():
                self.auth_error = (
                    "No hay base de datos. Revisa DATABASE_URL en .env."
                )
                return
            created = sb.user_repo().create_user(
                email=self.form_email,
                password=self.form_password,
                nombre=self.form_nombre,
            )
            self.usuario_id = int(created["id_usuario"])
            self.email = str(created.get("email") or "")
            self.nombre = str(created.get("nombre") or "")
            self.is_authenticated = True
            self.form_password = ""
            self.auth_info = "Cuenta creada."
            app = await self.get_state(AppState)
            app.tab = "practica"
            study = await self.get_state(StudyState)
            study.reset_all()
        except ValueError as exc:
            self.auth_error = str(exc)
        except Exception as exc:
            self.auth_error = f"No se pudo registrar: {exc}"
        finally:
            self.loading = False

    @rx.event
    async def logout(self):
        self.is_authenticated = False
        self.usuario_id = 0
        self.email = ""
        self.nombre = ""
        self.form_password = ""
        self.auth_mode = "login"
        study = await self.get_state(StudyState)
        study.reset_all()
        dash = await self.get_state(DashboardState)
        dash.clear()
        app = await self.get_state(AppState)
        app.tab = "practica"


# ---------------------------------------------------------------------------
# Navegación
# ---------------------------------------------------------------------------


class AppState(rx.State):
    """Pestaña activa del shell autenticado."""

    tab: str = "practica"  # guias | practica | simulacro | admin | dashboard

    @rx.event
    async def set_tab(self, tab: str):
        self.tab = tab
        if tab == "dashboard":
            dash = await self.get_state(DashboardState)
            auth = await self.get_state(AuthState)
            return dash.load(auth.usuario_id)
        if tab == "guias":
            ingest = await self.get_state(IngestState)
            ingest.mode = "student"
        if tab == "admin":
            ingest = await self.get_state(IngestState)
            ingest.mode = "admin"
        if tab == "practica":
            study = await self.get_state(StudyState)
            if not study.materias:
                return study.load_catalog()
        if tab in ("practica", "simulacro"):
            study = await self.get_state(StudyState)
            # Si venías de otro modo quiz, no mezclar fases salvo mismo mode
            if study.phase == "setup":
                pass


# ---------------------------------------------------------------------------
# Estudio (Práctica + Simulacro)
# ---------------------------------------------------------------------------


class StudyState(rx.State):
    """Práctica enfocada y Simulacro oficial."""

    # Catálogo
    materias: list[dict[str, Any]] = []
    temas: list[dict[str, Any]] = []
    materia_id: int = 0
    tema_id: int = 0
    fuente_banco: str = "todo"  # todo | oficial | mis_guias
    limite_practica: int = 12
    status_msg: str = ""
    error_msg: str = ""
    loading: bool = False

    # Bloque activo
    mode: str = ""  # "" | practica | simulacro
    phase: str = "setup"  # setup | quiz | results
    index: int = 0
    _preguntas: list[dict[str, Any]] = []
    answers: dict[str, str] = {}
    comprobadas: dict[str, bool] = {}
    check_ok: dict[str, bool] = {}
    feedback_inmediato: bool = True

    # Revelación SOLO tras check en práctica (nunca @rx.var de la clave viva)
    revealed_correct: str = ""
    revealed_explanation: str = ""

    # Coach IA one-shot (solo tras error, a petición del alumno)
    coach_loading: bool = False
    coach_text: str = ""
    coach_error: str = ""
    coach_qid: str = ""
    coach_nivel: int = 2
    coach_nivel_label: str = "Nivel 2 · Intermedio"
    coach_kpi_fuente: str = "sesion"  # sesion | tema | materia | global | cold_start
    _coach_cache: dict[str, str] = {}
    _coach_nivel_cache: dict[str, int] = {}
    # KPI histórico por segmento (sesión): evita repetir SQL en cada coach call
    _kpi_hist_cache: dict[str, dict[str, Any]] = {}

    # Persistencia incremental (práctica) / meta simulacro
    _persisted_qids: dict[str, bool] = {}
    _study_usuario_id: int = 0
    _simulacro_usuario_id: int = 0
    _simulacro_area: str = "BIOMEDICAS"
    _simulacro_seed: int = 0

    # Resultado
    result_correctas: int = 0
    result_incorrectas: int = 0
    result_en_blanco: int = 0
    result_bruto: float = 0.0
    result_ponderado: float = 0.0
    result_aciertos_pct: int = 0
    result_titulo: str = ""
    persist_status: str = ""

    # Simulacro meta
    simulacro_id_sesion: int = 0
    puntaje_maximo_ponderado: float = 0.0

    def reset_all(self):
        self.mode = ""
        self.phase = "setup"
        self.index = 0
        self._preguntas = []
        self.answers = {}
        self.comprobadas = {}
        self.check_ok = {}
        self.revealed_correct = ""
        self.revealed_explanation = ""
        self.coach_loading = False
        self.coach_text = ""
        self.coach_error = ""
        self.coach_qid = ""
        self.coach_nivel = 2
        self.coach_nivel_label = "Nivel 2 · Intermedio"
        self.coach_kpi_fuente = "sesion"
        self._coach_cache = {}
        self._coach_nivel_cache = {}
        self._kpi_hist_cache = {}
        self._persisted_qids = {}
        self._study_usuario_id = 0
        self._simulacro_usuario_id = 0
        self._simulacro_area = "BIOMEDICAS"
        self._simulacro_seed = 0
        self.status_msg = ""
        self.error_msg = ""
        self.persist_status = ""
        self.result_correctas = 0
        self.result_incorrectas = 0
        self.result_en_blanco = 0
        self.result_bruto = 0.0
        self.result_ponderado = 0.0
        self.result_aciertos_pct = 0
        self.simulacro_id_sesion = 0

    def _qid(self) -> str:
        if not self._preguntas:
            return ""
        return str(self._preguntas[self.index]["id_pregunta"])

    @rx.var
    def total_questions(self) -> int:
        return len(self._preguntas)

    @rx.var
    def question_number(self) -> int:
        return self.index + 1 if self._preguntas else 0

    @rx.var
    def progress_percent(self) -> int:
        n = len(self._preguntas)
        if not n:
            return 0
        return int(((self.index + 1) / n) * 100)

    @rx.var
    def marked_count(self) -> int:
        return len(self.answers)

    @rx.var
    def category(self) -> str:
        if not self._preguntas:
            return ""
        q = self._preguntas[self.index]
        return f"{q.get('materia_nombre', '')} · {q.get('tema_nombre', '')}"

    @rx.var
    def stem(self) -> str:
        if not self._preguntas:
            return ""
        return self._preguntas[self.index].get("enunciado", "")

    @rx.var
    def option_a(self) -> str:
        return self._opt("A")

    @rx.var
    def option_b(self) -> str:
        return self._opt("B")

    @rx.var
    def option_c(self) -> str:
        return self._opt("C")

    @rx.var
    def option_d(self) -> str:
        return self._opt("D")

    @rx.var
    def option_e(self) -> str:
        return self._opt("E")

    def _opt(self, letter: str) -> str:
        if not self._preguntas:
            return ""
        return (self._preguntas[self.index].get("alternativas") or {}).get(letter, "")

    @rx.var
    def selected(self) -> str:
        return self.answers.get(self._qid(), "")

    @rx.var
    def answered(self) -> bool:
        return bool(self.comprobadas.get(self._qid(), False))

    @rx.var
    def is_correct(self) -> bool:
        return bool(self.check_ok.get(self._qid(), False))

    # correct_answer / explanation eliminados como @rx.var (fuga en simulacro).
    # Usar revealed_correct / revealed_explanation (solo tras check_answer en práctica).

    @rx.var
    def is_first(self) -> bool:
        return self.index <= 0

    @rx.var
    def is_last_question(self) -> bool:
        n = len(self._preguntas)
        return n > 0 and self.index >= n - 1

    @rx.var
    def can_advance(self) -> bool:
        if not self.feedback_inmediato:
            return True
        return bool(self.comprobadas.get(self._qid(), False))

    @rx.var
    def has_selection(self) -> bool:
        return bool(self.answers.get(self._qid()))

    @rx.var
    def next_label(self) -> str:
        if self.is_last_question:
            return "Finalizar y evaluar"
        return "Siguiente"

    @rx.var
    def score_label(self) -> str:
        return f"{self.result_aciertos_pct}% de aciertos"

    @rx.var
    def result_subtitle(self) -> str:
        return (
            f"{self.result_correctas} correctas · "
            f"{self.result_incorrectas} incorrectas · "
            f"{self.result_en_blanco} en blanco"
        )

    @rx.var
    def result_bruto_label(self) -> str:
        return f"{self.result_bruto:.0f} pts brutos"

    @rx.var
    def result_ponderado_label(self) -> str:
        return f"{self.result_ponderado:.1f} pts ponderados"

    @rx.var
    def materia_options(self) -> list[str]:
        opts = ["0|Todas las materias (Modo Global)"]
        opts.extend(
            f"{m.get('id_materia')}|{m.get('nombre')}"
            for m in self.materias
        )
        return opts

    @rx.event
    def load_catalog(self):
        self.error_msg = ""
        if not sb.bootstrap_database():
            self.materias = []
            self.status_msg = "Catálogo no disponible (sin Neon). Usa demo local."
            return
        try:
            rows = sb.banco_repo().fetch_materias()
            self.materias = [dict(r) for r in rows]
            self.status_msg = (
                f"{len(self.materias)} materias + Modo Global disponibles."
            )
        except Exception as exc:
            self.error_msg = f"Catálogo: {exc}"

    @rx.event
    def set_fuente(self, value: str):
        self.fuente_banco = value

    @rx.event
    def set_limite(self, value: list[int | float]):
        if value:
            self.limite_practica = int(value[0])

    @rx.event
    def set_materia(self, value: str):
        # value = "id|nombre" · id=0 → Modo Global
        try:
            mid = int(str(value).split("|", 1)[0])
        except Exception:
            mid = 0
        self.materia_id = mid
        self.tema_id = 0
        self.temas = []
        if mid > 0 and sb.is_db_ready():
            try:
                self.temas = [dict(t) for t in sb.banco_repo().fetch_temas_by_materia(mid)]
            except Exception as exc:
                self.error_msg = str(exc)

    @rx.event
    def set_tema(self, value: str):
        try:
            self.tema_id = int(str(value).split("|", 1)[0])
        except Exception:
            self.tema_id = 0

    def _start_block(self, preguntas: list[dict[str, Any]], *, mode: str, feedback: bool):
        # Runtime shuffling: cada sesión mezcla A–E y remapea la correcta.
        self._preguntas = shuffle_bloque(preguntas)
        self.mode = mode
        self.feedback_inmediato = feedback
        self.phase = "quiz"
        self.index = 0
        self.answers = {}
        self.comprobadas = {}
        self.check_ok = {}
        self.revealed_correct = ""
        self.revealed_explanation = ""
        self.coach_loading = False
        self.coach_text = ""
        self.coach_error = ""
        self.coach_qid = ""
        self.coach_nivel = 2
        self.coach_nivel_label = "Nivel 2 · Intermedio"
        self.coach_kpi_fuente = "sesion"
        self._coach_cache = {}
        self._coach_nivel_cache = {}
        self._kpi_hist_cache = {}
        self._persisted_qids = {}
        self.persist_status = ""
        self.error_msg = ""

    def _clear_coach_ui(self):
        self.coach_loading = False
        self.coach_text = ""
        self.coach_error = ""
        self.coach_qid = ""
        self.coach_nivel = 2
        self.coach_nivel_label = "Nivel 2 · Intermedio"
        self.coach_kpi_fuente = "sesion"

    def _set_coach_nivel_ui(self, nivel: int, *, fuente: str = "sesion"):
        from app.services.error_coach_service import (
            label_nivel_tutor,
            normalize_nivel_tutor,
        )

        n = normalize_nivel_tutor(nivel)
        self.coach_nivel = n
        self.coach_nivel_label = label_nivel_tutor(n)
        self.coach_kpi_fuente = str(fuente or "sesion")

    def _nivel_tutor_sesion_actual(self) -> int:
        """% aciertos de preguntas ya comprobadas en esta sesión → nivel 1|2|3."""
        from app.services.error_coach_service import calcular_nivel_desde_sesion

        comprobadas = sum(1 for v in self.comprobadas.values() if v)
        correctas = sum(
            1
            for qid, done in self.comprobadas.items()
            if done and self.check_ok.get(qid, False)
        )
        return calcular_nivel_desde_sesion(
            comprobadas=comprobadas,
            correctas=correctas,
            minimo_muestra=2,
        )

    def _refresh_reveal_for_current(self):
        """Restaura revelación + coach cacheado solo si ya se comprobó en práctica."""
        if not self.feedback_inmediato or not self._preguntas:
            self.revealed_correct = ""
            self.revealed_explanation = ""
            self._clear_coach_ui()
            return
        qid = self._qid()
        if not self.comprobadas.get(qid):
            self.revealed_correct = ""
            self.revealed_explanation = ""
            self._clear_coach_ui()
            return
        q = self._preguntas[self.index]
        self.revealed_correct = str(q.get("alternativa_correcta") or "")
        self.revealed_explanation = str(q.get("justificacion") or "")
        cached = self._coach_cache.get(qid, "")
        self.coach_qid = qid if cached else ""
        self.coach_text = cached
        self.coach_error = ""
        self.coach_loading = False
        if cached:
            self._set_coach_nivel_ui(self._coach_nivel_cache.get(qid, 2))
        else:
            self._set_coach_nivel_ui(2)

    async def _resolver_nivel_tutor_adaptativo(
        self,
        *,
        usuario_id: int,
        tema_id: int,
        materia_id: int,
        nivel_sesion: int,
    ) -> tuple[int, str]:
        """
        KPI histórico (tema→materia→global) con fallback a % de sesión (cold start).
        """
        from app.services.error_coach_service import nivel_desde_precision_pct

        if usuario_id <= 0 or not sb.is_db_ready():
            return nivel_sesion, "sesion"

        cache_key = f"u{usuario_id}|t{tema_id}|m{materia_id}"
        async with self:
            cached = self._kpi_hist_cache.get(cache_key)
            if cached and not cached.get("cold_start", True):
                return int(cached["nivel"]), str(cached.get("segmento") or "global")

        try:
            kpi = await asyncio.to_thread(
                sb.engine().calcular_kpi_historico_aciertos,
                usuario_id=usuario_id,
                tema_id=tema_id if tema_id > 0 else None,
                materia_id=materia_id if materia_id > 0 else None,
                ventana=100,
                minimo_muestra=10,
            )
        except Exception:
            return nivel_sesion, "sesion"

        cold = bool(getattr(kpi, "cold_start", True))
        segmento = str(getattr(kpi, "segmento", "cold_start") or "cold_start")
        if cold:
            # < 10 intentos en tema/materia/global → rendimiento de la sesión
            return nivel_sesion, "sesion"

        nivel = nivel_desde_precision_pct(float(getattr(kpi, "precision_pct", 0.0)))
        async with self:
            hist = dict(self._kpi_hist_cache)
            hist[cache_key] = {
                "nivel": nivel,
                "segmento": segmento,
                "cold_start": False,
                "precision_pct": float(getattr(kpi, "precision_pct", 0.0)),
                "intentos": int(getattr(kpi, "intentos", 0)),
            }
            self._kpi_hist_cache = hist
        return nivel, segmento

    @rx.var
    def coach_visible(self) -> bool:
        return bool(self.coach_text) and self.coach_qid == self._qid()

    @rx.var
    def can_request_coach(self) -> bool:
        """Solo práctica, tras error comprobado, sin carga activa."""
        if not self.feedback_inmediato or self.phase != "quiz":
            return False
        qid = self._qid()
        if not qid or not self.comprobadas.get(qid):
            return False
        if self.check_ok.get(qid, False):
            return False
        if self.coach_loading:
            return False
        if self.coach_text and self.coach_qid == qid:
            return False
        return True

    @rx.event
    async def start_practica_local(self):
        """Fallback sin Neon — banco local A–E."""
        qs = [dict(q) for q in LOCAL_QUESTIONS]
        # Adapt shape
        adapted = []
        for q in qs:
            adapted.append(
                {
                    "id_pregunta": q["id_pregunta"],
                    "orden": q["id_pregunta"],
                    "materia_nombre": q["materia_nombre"],
                    "tema_nombre": q["tema_nombre"],
                    "enunciado": q["enunciado"],
                    "alternativas": dict(q["alternativas"]),
                    "alternativa_correcta": q["alternativa_correcta"],
                    "justificacion": q["justificacion"],
                    "factor_ponderacion": float(q["factor_ponderacion"]),
                }
            )
        self._start_block(adapted, mode="practica", feedback=True)
        self.status_msg = "Práctica demo local iniciada (alternativas mezcladas)."

    @rx.event
    async def start_practica(self):
        self.loading = True
        self.error_msg = ""
        yield
        auth = await self.get_state(AuthState)
        try:
            if not sb.bootstrap_database():
                qs = []
                for q in LOCAL_QUESTIONS:
                    qs.append(
                        {
                            "id_pregunta": q["id_pregunta"],
                            "orden": q["id_pregunta"],
                            "materia_nombre": q["materia_nombre"],
                            "tema_nombre": q["tema_nombre"],
                            "enunciado": q["enunciado"],
                            "alternativas": dict(q["alternativas"]),
                            "alternativa_correcta": q["alternativa_correcta"],
                            "justificacion": q["justificacion"],
                            "factor_ponderacion": float(q["factor_ponderacion"]),
                        }
                    )
                self._start_block(qs, mode="practica", feedback=True)
                self.status_msg = "Práctica demo local iniciada (alternativas mezcladas)."
                return
            # materia_id == 0 → Todas las materias (Modo Global)
            mid = self.materia_id if self.materia_id > 0 else None
            tid = self.tema_id if self.tema_id > 0 else None
            practica = sb.engine().generar_practica_enfocada(
                usuario_id=auth.usuario_id,
                materia_id=mid,
                tema_id=tid,
                limite=int(self.limite_practica),
                fuente_banco=self.fuente_banco,
            )
            preguntas = [sb.pregunta_to_public(p) for p in practica.preguntas]
            preguntas = sb.hydrate_justificaciones(preguntas)
            self._study_usuario_id = int(auth.usuario_id or 0)
            self._start_block(preguntas, mode="practica", feedback=True)
            modo = "Global" if mid is None and tid is None else "Enfocada"
            self.status_msg = (
                f"Práctica {modo}: {len(preguntas)} ítems · opciones A–E mezcladas · "
                "progreso guardado por pregunta."
            )
        except Exception as exc:
            self.error_msg = str(exc)
        finally:
            self.loading = False

    @rx.event
    async def start_practica_debilidades(self):
        self.loading = True
        self.error_msg = ""
        yield
        auth = await self.get_state(AuthState)
        try:
            if not sb.bootstrap_database():
                self.error_msg = "Debilidades requiere Neon + historial."
                return
            practica = sb.engine().generar_practica_debilidades(
                usuario_id=auth.usuario_id,
                limite=int(self.limite_practica),
                fuente_banco=self.fuente_banco,
            )
            preguntas = [sb.pregunta_to_public(p) for p in practica.preguntas]
            preguntas = sb.hydrate_justificaciones(preguntas)
            self._study_usuario_id = int(auth.usuario_id or 0)
            self._start_block(preguntas, mode="practica", feedback=True)
            self.status_msg = (
                f"Debilidades: {len(preguntas)} ítems · guardado incremental activo."
            )
        except Exception as exc:
            self.error_msg = str(exc)
        finally:
            self.loading = False

    @rx.event
    async def start_simulacro(self):
        self.loading = True
        self.error_msg = ""
        yield
        auth = await self.get_state(AuthState)
        try:
            if not sb.bootstrap_database():
                self.error_msg = "El simulacro oficial requiere Neon con banco estratificado."
                return
            simulacro = sb.engine().generar_simulacro_oficial(
                usuario_id=auth.usuario_id,
                persistir_sesion=True,
                fuente_banco=self.fuente_banco,
            )
            preguntas = [sb.pregunta_to_public(p) for p in simulacro.preguntas]
            self.simulacro_id_sesion = int(getattr(simulacro, "id_sesion", 0) or 0)
            self.puntaje_maximo_ponderado = float(
                getattr(simulacro, "puntaje_maximo_ponderado", 0) or 0
            )
            self._simulacro_usuario_id = int(auth.usuario_id or 0)
            self._simulacro_area = str(getattr(simulacro, "area_examen", "BIOMEDICAS") or "BIOMEDICAS")
            self._simulacro_seed = int(getattr(simulacro, "seed_muestreo", 0) or 0)
            # Simulacro: sin feedback inmediato (integridad) + shuffle A–E
            self._start_block(preguntas, mode="simulacro", feedback=False)
            self.status_msg = (
                f"Simulacro {len(preguntas)} Q · sesión #{self.simulacro_id_sesion} "
                "· opciones A–E mezcladas · evaluación al finalizar"
            )
        except Exception as exc:
            self.error_msg = str(exc)
        finally:
            self.loading = False

    @rx.event
    def select_option(self, letter: str):
        if self.phase != "quiz" or not self._preguntas:
            return
        qid = self._qid()
        if self.feedback_inmediato and self.comprobadas.get(qid):
            return
        letra = str(letter).strip().upper()
        if letra not in {"A", "B", "C", "D", "E"}:
            return
        updated = dict(self.answers)
        updated[qid] = letra
        self.answers = updated

    @rx.event
    def check_answer(self):
        """Evalúa en práctica, revela feedback y encola persistencia incremental."""
        if not self.feedback_inmediato or self.phase != "quiz":
            return
        qid = self._qid()
        if self.comprobadas.get(qid):
            return
        marcada = self.answers.get(qid)
        if not marcada:
            return
        q = self._preguntas[self.index]
        correcta = str(q.get("alternativa_correcta") or "")
        es_ok = str(marcada).upper() == correcta.upper()
        comp = dict(self.comprobadas)
        ok_map = dict(self.check_ok)
        comp[qid] = True
        ok_map[qid] = es_ok
        self.comprobadas = comp
        self.check_ok = ok_map
        # Revelación solo aquí (práctica). En simulacro feedback_inmediato=False.
        self.revealed_correct = correcta
        self.revealed_explanation = str(q.get("justificacion") or "")
        # Reset coach UI (el cache por qid evita rellamar OpenRouter si vuelve)
        self.coach_loading = False
        self.coach_error = ""
        cached = self._coach_cache.get(qid, "")
        self.coach_qid = qid if cached else ""
        self.coach_text = cached
        if cached:
            self._set_coach_nivel_ui(self._coach_nivel_cache.get(qid, 2))
        else:
            self._set_coach_nivel_ui(2)
        return StudyState.persist_current_practica_intento

    @rx.event(background=True)
    async def request_error_coach(self):
        """Genera coaching IA one-shot tras un error (OpenRouter, no bloquea UI)."""
        async with self:
            if not self.feedback_inmediato or self.phase != "quiz":
                return
            if not self._preguntas:
                return
            qid = self._qid()
            if not qid or not self.comprobadas.get(qid):
                return
            if self.check_ok.get(qid, False):
                return
            if self.coach_loading:
                return
            cached = self._coach_cache.get(qid, "")
            if cached:
                self.coach_qid = qid
                self.coach_text = cached
                self.coach_error = ""
                self._set_coach_nivel_ui(self._coach_nivel_cache.get(qid, 2))
                return

            nivel_sesion = self._nivel_tutor_sesion_actual()
            marcada = self.answers.get(qid, "")
            q = dict(self._preguntas[self.index])
            usuario_id = int(self._study_usuario_id or 0)
            tema_id = int(q.get("tema_id") or 0)
            materia_id = int(q.get("materia_id") or 0)
            self.coach_loading = True
            self.coach_error = ""
            self.coach_text = ""
            self.coach_qid = qid
            self._set_coach_nivel_ui(nivel_sesion, fuente="sesion")

        if usuario_id <= 0:
            auth = await self.get_state(AuthState)
            usuario_id = int(auth.usuario_id or 0)

        # KPI histórico consolidado; cold start → % sesión (ya calculado)
        nivel_tutor, fuente = await self._resolver_nivel_tutor_adaptativo(
            usuario_id=usuario_id,
            tema_id=tema_id,
            materia_id=materia_id,
            nivel_sesion=nivel_sesion,
        )
        async with self:
            self._set_coach_nivel_ui(nivel_tutor, fuente=fuente)
            if usuario_id > 0:
                self._study_usuario_id = usuario_id

        alts = q.get("alternativas") or {}
        tema = (
            str(q.get("tema_nombre") or "").strip()
            or str(q.get("materia_nombre") or "").strip()
            or "Sin tema"
        )
        try:
            reply = await asyncio.to_thread(
                sb.explicar_error_alumno,
                pregunta_id=int(q["id_pregunta"]),
                enunciado=str(q.get("enunciado") or ""),
                alternativas=dict(alts) if isinstance(alts, dict) else {},
                alternativa_correcta=str(q.get("alternativa_correcta") or ""),
                alternativa_alumno=str(marcada or ""),
                justificacion=str(q.get("justificacion") or ""),
                tema_o_materia=tema,
                usuario_id=usuario_id if usuario_id > 0 else None,
                nivel_tutor=nivel_tutor,
            )
            texto = str(getattr(reply, "texto", "") or "").strip()
            if not texto:
                raise RuntimeError("El tutor no devolvió texto")
            nivel_resp = int(getattr(reply, "nivel_tutor", nivel_tutor) or nivel_tutor)
            async with self:
                cache = dict(self._coach_cache)
                cache[qid] = texto
                self._coach_cache = cache
                ncache = dict(self._coach_nivel_cache)
                ncache[qid] = nivel_resp
                self._coach_nivel_cache = ncache
                if self._qid() != qid:
                    self.coach_loading = False
                    return
                self.coach_text = texto
                self.coach_qid = qid
                self.coach_error = ""
                self.coach_loading = False
                self._set_coach_nivel_ui(nivel_resp, fuente=fuente)
                if usuario_id > 0:
                    self._study_usuario_id = usuario_id
        except Exception as exc:
            async with self:
                self.coach_loading = False
                self.coach_error = (
                    "No pude generar el coaching ahora. "
                    f"Revisa la justificación oficial e inténtalo de nuevo. ({exc})"
                )

    @rx.event(background=True)
    async def persist_current_practica_intento(self):
        """Guarda 1 intento en historial_intentos (SRS/debilidades) sin esperar finalize."""
        async with self:
            if self.mode != "practica" or self.phase != "quiz":
                return
            if not self._preguntas:
                return
            qid = self._qid()
            if not qid or self._persisted_qids.get(qid):
                return
            if not self.comprobadas.get(qid):
                return
            marcada_display = self.answers.get(qid)
            if not marcada_display:
                return
            q = dict(self._preguntas[self.index])
            usuario_id = int(self._study_usuario_id or 0)
            if usuario_id <= 0:
                auth = await self.get_state(AuthState)
                usuario_id = int(auth.usuario_id or 0)
                self._study_usuario_id = usuario_id

        if usuario_id <= 0 or not sb.is_db_ready():
            return

        # Remap display → letra del banco para el ledger
        shuffle_map = q.get("shuffle_map") or {}
        marcada_banco = shuffle_map.get(marcada_display, marcada_display)
        try:
            await asyncio.to_thread(
                sb.engine().registrar_intento_practica,
                usuario_id=usuario_id,
                pregunta_id=int(q["id_pregunta"]),
                alternativa_marcada=marcada_display,
                alternativa_correcta=str(q.get("alternativa_correcta") or ""),
                factor_ponderacion=float(q.get("factor_ponderacion") or 1.0),
                orden_en_sesion=int(q.get("orden") or 0) or None,
                tiempo_respuesta_ms=0,
                alternativa_marcada_banco=marcada_banco,
            )
            async with self:
                marked = dict(self._persisted_qids)
                marked[qid] = True
                self._persisted_qids = marked
                self.persist_status = f"Guardado · pregunta #{qid}"
        except Exception as exc:
            async with self:
                self.persist_status = f"No se pudo guardar intento: {exc}"

    @rx.event
    def go_prev(self):
        if self.index > 0:
            self.index -= 1
            self._refresh_reveal_for_current()

    @rx.event
    def go_next(self):
        if self.feedback_inmediato and not self.comprobadas.get(self._qid()):
            return
        if self.index >= len(self._preguntas) - 1:
            return StudyState.finalize
        self.index += 1
        self._refresh_reveal_for_current()

    @staticmethod
    def _preguntas_a_tutor(preguntas: list[dict[str, Any]]):
        from app.services.tutor_engine import PreguntaTutor

        out = []
        for q in preguntas:
            out.append(
                PreguntaTutor(
                    orden=int(q.get("orden") or 0),
                    id_pregunta=int(q["id_pregunta"]),
                    materia_id=int(q.get("materia_id") or 0),
                    materia_codigo=int(q.get("materia_codigo") or 0),
                    materia_nombre=str(q.get("materia_nombre") or ""),
                    factor_ponderacion=float(q.get("factor_ponderacion") or 1.0),
                    tema_id=int(q.get("tema_id") or 0),
                    tema_nombre=str(q.get("tema_nombre") or ""),
                    enunciado=str(q.get("enunciado") or ""),
                    alternativas=dict(q.get("alternativas") or {}),
                    # Usa la correcta YA remapeada (coherente con answers[display])
                    alternativa_correcta=str(q.get("alternativa_correcta") or ""),
                    peso_prioridad=0.0,
                )
            )
        return out

    @rx.event(background=True)
    async def finalize(self):
        if not self._preguntas:
            return

        async with self:
            preguntas = [dict(q) for q in self._preguntas]
            answers = dict(self.answers)
            mode = self.mode
            sesion_id = int(self.simulacro_id_sesion or 0)
            uid_sim = int(self._simulacro_usuario_id or 0)
            area = self._simulacro_area or "BIOMEDICAS"
            seed = int(self._simulacro_seed or 0)
            techo = float(self.puntaje_maximo_ponderado or 0)
            auth = await self.get_state(AuthState)
            if uid_sim <= 0:
                uid_sim = int(auth.usuario_id or 0)

        resumen: dict[str, Any]
        persist_note = ""

        if mode == "simulacro" and sb.is_db_ready() and sesion_id > 0 and uid_sim > 0:
            try:
                from app.services.tutor_engine import SimulacroOficial

                # Respuestas para el motor: letras de DISPLAY (mismas que alternativa_correcta remapeada)
                simulacro = SimulacroOficial(
                    id_sesion=sesion_id,
                    usuario_id=uid_sim,
                    area_examen=area,
                    seed_muestreo=seed,
                    total_preguntas=len(preguntas),
                    puntaje_maximo_ponderado=techo,
                    preguntas=StudyState._preguntas_a_tutor(preguntas),
                )
                cierre = await asyncio.to_thread(
                    sb.engine().finalizar_simulacro_oficial,
                    simulacro=simulacro,
                    respuestas=answers,
                    tiempos_ms={},
                )
                total = max(1, int(cierre.correctas + cierre.incorrectas + cierre.en_blanco))
                resumen = {
                    "correctas": int(cierre.correctas),
                    "incorrectas": int(cierre.incorrectas),
                    "en_blanco": int(cierre.en_blanco),
                    "puntaje_bruto": float(cierre.puntaje_bruto),
                    "puntaje_ponderado": float(cierre.puntaje_ponderado),
                    "aciertos_pct": int(round(100 * cierre.correctas / total)),
                }
                persist_note = f"Simulacro #{sesion_id} guardado ({cierre.n_insertados} intentos)."
            except Exception as exc:
                resumen = evaluar_bloque(preguntas, answers)
                persist_note = f"Cierre local; fallo al persistir simulacro: {exc}"
        else:
            # Práctica: intentos ya fueron insertados incrementalmente.
            resumen = evaluar_bloque(preguntas, answers)
            if mode == "practica":
                persist_note = (
                    "Práctica: intentos guardados de forma incremental durante la sesión."
                )

        async with self:
            self.result_correctas = int(resumen["correctas"])
            self.result_incorrectas = int(resumen["incorrectas"])
            self.result_en_blanco = int(resumen["en_blanco"])
            self.result_bruto = float(resumen["puntaje_bruto"])
            self.result_ponderado = float(resumen["puntaje_ponderado"])
            self.result_aciertos_pct = int(resumen["aciertos_pct"])
            pct = self.result_aciertos_pct
            if pct >= 75:
                self.result_titulo = "¡Excelente rendimiento!"
            elif pct >= 50:
                self.result_titulo = "Buen avance"
            else:
                self.result_titulo = "Sigue practicando"
            self.revealed_correct = ""
            self.revealed_explanation = ""
            self.coach_loading = False
            self.coach_text = ""
            self.coach_error = ""
            self.coach_qid = ""
            self.coach_nivel = 2
            self.coach_nivel_label = "Nivel 2 · Intermedio"
            self.coach_kpi_fuente = "sesion"
            self.persist_status = persist_note
            self.phase = "results"

    @rx.event
    def back_to_setup(self):
        self.phase = "setup"
        self.mode = ""
        self._preguntas = []
        self.index = 0
        self.answers = {}
        self.comprobadas = {}
        self.check_ok = {}
        self.revealed_correct = ""
        self.revealed_explanation = ""
        self.coach_loading = False
        self.coach_text = ""
        self.coach_error = ""
        self.coach_qid = ""
        self.coach_nivel = 2
        self.coach_nivel_label = "Nivel 2 · Intermedio"
        self.coach_kpi_fuente = "sesion"
        self._coach_cache = {}
        self._coach_nivel_cache = {}
        self._kpi_hist_cache = {}
        self._persisted_qids = {}
        self.persist_status = ""


# ---------------------------------------------------------------------------
# Mis Guías / Admin
# ---------------------------------------------------------------------------


class IngestState(rx.State):
    """Carga de PDF/imágenes → revisión → guardado diferido.

    Flujo:
      subir → multi-rango (mismo PDF) → analizar (background, persist=False)
      → revisar/editar → confirmar en bóveda.
    """

    mode: str = "student"  # student | admin
    stage: str = "idle"  # idle | ready | review | done
    status: str = ""
    error: str = ""
    loading: bool = False
    last_insertadas: int = 0
    last_duplicadas: int = 0
    last_warnings: str = ""
    uploaded_name: str = ""
    progress_pct: int = 0
    progress_detail: str = ""

    file_meta: list[dict[str, Any]] = []
    has_pdf: bool = False
    n_files: int = 0
    pdf_total_pages: int = 0

    # Borrador de rango + lista acumulada
    page_start: int = 1
    page_end: int = 10
    range_label: str = ""
    range_error: str = ""
    page_ranges: list[dict[str, Any]] = []
    _range_seq: int = 0

    # Revisión post-ingesta
    pending_items: list[dict[str, Any]] = []
    area_summary: str = ""
    materias_options: list[str] = []
    editing_uid: str = ""
    edit_enunciado: str = ""
    edit_justificacion: str = ""
    edit_materia: str = ""
    edit_tema: str = ""
    edit_tag: str = ""
    edit_nivel: str = "intermedia"
    edit_correcta: str = "A"
    edit_a: str = ""
    edit_b: str = ""
    edit_c: str = ""
    edit_d: str = ""
    edit_e: str = ""

    _pending_paths: list[str] = []
    _job_running: bool = False
    _item_seq: int = 0

    @rx.event
    def set_mode_student(self):
        self.mode = "student"

    @rx.event
    def set_mode_admin(self):
        self.mode = "admin"

    @rx.var
    def can_analyze(self) -> bool:
        # El borrador de rango puede estar inválido; basta con rangos en cola.
        return (
            self.stage in ("ready", "review")
            and self.n_files > 0
            and len(self.page_ranges) > 0
            and not self.loading
        )

    @rx.var
    def show_page_range(self) -> bool:
        return self.has_pdf and self.stage in ("ready", "review") and self.n_files > 0

    @rx.var
    def show_review(self) -> bool:
        return self.stage in ("review", "done") and len(self.pending_items) > 0

    @rx.var
    def pdf_pages_banner(self) -> str:
        if not self.has_pdf:
            return ""
        return (
            f"Documento activo: {self.pdf_total_pages} páginas. "
            "Agrega uno o más rangos sin volver a subir el archivo."
        )

    @rx.var
    def pending_count(self) -> int:
        return len(self.pending_items)

    @rx.var
    def ranges_count(self) -> int:
        return len(self.page_ranges)

    def _reset_queue(self, *, keep_file: bool = False):
        if not keep_file:
            for p in self._pending_paths:
                try:
                    Path(p).unlink(missing_ok=True)
                except Exception:
                    pass
            self._pending_paths = []
            self.file_meta = []
            self.has_pdf = False
            self.n_files = 0
            self.pdf_total_pages = 0
            self.uploaded_name = ""
            self.page_ranges = []
            self._range_seq = 0
            self.stage = "idle"
        self.page_start = 1
        self.page_end = 10
        self.range_label = ""
        self.range_error = ""

    def _refresh_range_label(self):
        if self.range_error:
            self.range_label = ""
            return
        n = max(0, self.page_end - self.page_start + 1)
        if self.has_pdf:
            self.range_label = (
                f"Borrador: págs. {self.page_start}–{self.page_end} "
                f"({n} de {self.pdf_total_pages}). Pulsa «Agregar rango»."
            )
        else:
            self.range_label = "Imágenes listas — el análisis no usa rangos."

    def _validate_range(self):
        self.range_error = ""
        if not self.has_pdf:
            self._refresh_range_label()
            return
        total = max(1, int(self.pdf_total_pages or 1))
        start = max(1, int(self.page_start or 1))
        end = max(1, int(self.page_end or 1))
        start = min(start, total)
        end = min(end, total)
        self.page_start = start
        self.page_end = end
        if end < start:
            self.range_error = (
                f"La página final ({end}) no puede ser menor que la de inicio ({start})."
            )
        self._refresh_range_label()

    def _recompute_area_summary(self):
        counts: dict[str, int] = {}
        for it in self.pending_items:
            mat = str(it.get("materia_nombre") or "Sin materia")
            counts[mat] = counts.get(mat, 0) + 1
        if not counts:
            self.area_summary = "Sin ítems pendientes."
            return
        parts = [f"{v} {k}" for k, v in sorted(counts.items(), key=lambda x: (-x[1], x[0]))]
        self.area_summary = "Distribución por área: " + ", ".join(parts)

    @staticmethod
    def _pdf_page_count(data: bytes) -> int:
        try:
            import fitz

            doc = fitz.open(stream=data, filetype="pdf")
            n = int(doc.page_count or 0)
            doc.close()
            return max(0, n)
        except Exception:
            try:
                from io import BytesIO

                from pypdf import PdfReader

                return max(0, len(PdfReader(BytesIO(data)).pages))
            except Exception:
                return 0

    @rx.event
    def set_page_start(self, value: str):
        try:
            self.page_start = int(str(value).strip() or "1")
        except ValueError:
            self.page_start = 1
        self._validate_range()

    @rx.event
    def set_page_end(self, value: str):
        try:
            self.page_end = int(str(value).strip() or "1")
        except ValueError:
            self.page_end = 1
        self._validate_range()

    @rx.event
    def add_page_range(self):
        """Acumula un rango sobre el PDF activo sin re-subirlo."""
        if not self.has_pdf:
            # Para imágenes: un rango simbólico 1-1
            if self.n_files <= 0:
                self.error = "Sube un archivo primero."
                return
            if self.page_ranges:
                return
            self.page_ranges = [
                {"id": "img", "start": 1, "end": 1, "label": "Imágenes (lote completo)"}
            ]
            self.status = "Lote de imágenes listo para analizar."
            return

        self._validate_range()
        if self.range_error:
            return
        self._range_seq += 1
        rid = f"r{self._range_seq}"
        start, end = int(self.page_start), int(self.page_end)
        label = f"Rango {len(self.page_ranges) + 1}: págs. {start}–{end}"
        ranges = list(self.page_ranges)
        # Evitar duplicados exactos
        for r in ranges:
            if int(r.get("start", 0)) == start and int(r.get("end", 0)) == end:
                self.error = f"Ese rango ({start}–{end}) ya está en la lista."
                return
        ranges.append({"id": rid, "start": start, "end": end, "label": label})
        self.page_ranges = ranges
        self.error = ""
        self.status = f"{len(ranges)} rango(s) listo(s). Puedes agregar otro o analizar."
        # Preparar siguiente borrador sugerido
        nxt = min(self.pdf_total_pages, end + 1)
        self.page_start = nxt
        self.page_end = min(self.pdf_total_pages, nxt + 9)
        self._validate_range()

    @rx.event
    def remove_page_range(self, range_id: str):
        self.page_ranges = [r for r in self.page_ranges if str(r.get("id")) != str(range_id)]
        self.status = f"{len(self.page_ranges)} rango(s) restantes."

    @rx.event
    def keep_document_add_range(self):
        """Tras una revisión, vuelve a configurar rangos del mismo PDF."""
        if not self._pending_paths and self.n_files == 0:
            self.error = "No hay documento activo."
            return
        self.stage = "ready"
        self.status = (
            "Documento aún en memoria. Agrega otro rango y vuelve a analizar "
            "(los ítems actuales de revisión se mantienen hasta que confirmes o limpies)."
        )
        self.error = ""
        self.progress_pct = 0
        self.progress_detail = ""

    @rx.event
    def clear_files(self):
        self._reset_queue(keep_file=False)
        self.pending_items = []
        self.area_summary = ""
        self.status = ""
        self.error = ""
        self.last_warnings = ""
        self.last_insertadas = 0
        self.last_duplicadas = 0
        self.progress_pct = 0
        self.progress_detail = ""
        self.loading = False
        self._job_running = False
        self.editing_uid = ""

    @rx.event
    async def prepare_files(self, files: list[rx.UploadFile]):
        self.loading = True
        self.error = ""
        self.status = "Leyendo archivo(s)…"
        self.last_warnings = ""
        yield
        try:
            self._reset_queue(keep_file=False)
            self.pending_items = []
            self.area_summary = ""
            if not files:
                self.error = "Selecciona al menos un archivo."
                self.status = ""
                return
            if len(files) > 3:
                self.error = "Máximo 3 archivos por lote."
                self.status = ""
                return

            upload_dir = rx.get_upload_dir()
            upload_dir.mkdir(parents=True, exist_ok=True)

            meta: list[dict[str, Any]] = []
            paths: list[str] = []
            max_pages = 0
            has_pdf = False

            for idx, f in enumerate(files[:3]):
                data = await f.read()
                name = f.filename or f"archivo_{idx}"
                ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""
                kind = "other"
                pages = 0
                if ext in {"png", "jpg", "jpeg"}:
                    kind = "image"
                elif ext == "pdf":
                    kind = "pdf"
                    has_pdf = True
                    pages = self._pdf_page_count(data)
                    max_pages = max(max_pages, pages)
                else:
                    self.error = f"Formato no soportado: {name}"
                    self.status = ""
                    return

                safe_name = name.replace("/", "_").replace("\\", "_")
                path = upload_dir / f"{idx}_{safe_name}"
                path.write_bytes(data)
                paths.append(str(path))
                meta.append(
                    {
                        "name": name,
                        "kind": kind,
                        "pages": pages,
                        "label": (
                            f"{name} · {pages} páginas"
                            if kind == "pdf"
                            else f"{name} · imagen"
                        ),
                    }
                )

            self._pending_paths = paths
            self.file_meta = meta
            self.n_files = len(meta)
            self.has_pdf = has_pdf
            self.pdf_total_pages = int(max_pages)
            self.uploaded_name = meta[0]["name"] if meta else ""
            self.page_ranges = []

            if has_pdf and max_pages > 0:
                self.page_start = 1
                self.page_end = min(10, max_pages)
                self._validate_range()
                self.stage = "ready"
                self.status = (
                    f"PDF con {max_pages} páginas. Define rangos "
                    "(ej. 10–25, luego 80–85) y analiza."
                )
            else:
                self.page_ranges = [
                    {
                        "id": "img",
                        "start": 1,
                        "end": 1,
                        "label": "Imágenes (lote completo)",
                    }
                ]
                self.stage = "ready"
                self.status = f"{len(meta)} imagen(es) lista(s) para analizar."
        except Exception as exc:
            self.error = f"No se pudieron preparar los archivos: {exc}"
            self.status = ""
            self._reset_queue(keep_file=False)
        finally:
            self.loading = False

    @rx.event
    def analyze_files(self):
        if self._job_running or self.loading:
            self.error = "Ya hay un análisis en curso."
            return
        if self.n_files <= 0 or not self._pending_paths:
            self.error = "Primero sube un archivo."
            return
        if self.has_pdf and not self.page_ranges:
            self.error = "Agrega al menos un rango de páginas."
            return
        if not self.has_pdf and not self.page_ranges:
            self.page_ranges = [
                {"id": "img", "start": 1, "end": 1, "label": "Imágenes"}
            ]
        self.loading = True
        self.error = ""
        self.progress_pct = 0
        self.progress_detail = "Encolando análisis…"
        self.status = "Análisis en segundo plano (densidad adaptativa)…"
        return IngestState.run_analysis_job

    @staticmethod
    def _process_one_file_sync(
        *,
        path_str: str,
        name: str,
        kind: str,
        file_pages: int,
        ranges: list[dict[str, Any]],
        propietario: int | None,
    ) -> dict[str, Any]:
        from app.services.banco_extraction_service import (
            extract_banco_preguntas_from_chunks,
            extract_banco_preguntas_from_images,
        )
        from app.services.pdf_processor import process_pdf

        path = Path(path_str)
        if not path.exists():
            return {
                "ok": False,
                "warning": f"Archivo perdido: {path.name}",
                "items": [],
                "warnings": [],
            }

        data = path.read_bytes()
        items: list[dict[str, Any]] = []
        warnings: list[str] = []

        if kind == "image":
            result = extract_banco_preguntas_from_images(
                [{"bytes": data, "nombre": name}],
                materia_id=None,
                auto_clasificar=True,
                origen_contenido="imagen",
                fuente="openrouter",
                nombre_archivo_fuente=name,
                propietario_usuario_id=propietario,
                persist=False,
                max_items_per_image=12,
            )
            payload = result.to_dict()
            items.extend(list(payload.get("items") or []))
            warnings.extend([str(w) for w in (payload.get("warnings") or [])[:5]])
        elif kind == "pdf":
            if not ranges:
                return {
                    "ok": False,
                    "warning": f"{name}: sin rangos",
                    "items": [],
                    "warnings": [],
                }
            for r in ranges:
                start = int(r.get("start") or 1)
                end = int(r.get("end") or 1)
                if file_pages > 0:
                    start = max(1, min(start, file_pages))
                    end = max(1, min(end, file_pages))
                    if end < start:
                        end = start
                doc = process_pdf(
                    data,
                    allow_ocr=True,
                    source_filename=name,
                    page_start=start,
                    page_end=end,
                )
                result = extract_banco_preguntas_from_chunks(
                    doc.chunks,
                    materia_id=None,
                    auto_clasificar=True,
                    origen_contenido="pdf",
                    fuente="openrouter",
                    nombre_archivo_fuente=name,
                    propietario_usuario_id=propietario,
                    persist=False,
                    pause_between_chunks_s=0.35,
                    max_items_per_chunk=12,
                )
                payload = result.to_dict()
                batch = list(payload.get("items") or [])
                for it in batch:
                    it["rango_origen"] = f"{start}–{end}"
                items.extend(batch)
                warnings.extend([str(w) for w in (payload.get("warnings") or [])[:3]])
        else:
            return {
                "ok": False,
                "warning": f"{name}: formato no soportado",
                "items": [],
                "warnings": [],
            }

        return {"ok": True, "warning": "", "items": items, "warnings": warnings}

    @rx.event(background=True)
    async def run_analysis_job(self):
        async with self:
            if self._job_running:
                return
            self._job_running = True
            paths = list(self._pending_paths)
            meta_list = [dict(m) for m in self.file_meta]
            ranges = [dict(r) for r in self.page_ranges]
            mode = str(self.mode)
            existing = [dict(x) for x in self.pending_items]
            self.loading = True
            self.error = ""
            self.progress_pct = 3
            self.progress_detail = "Conectando servicios…"
            self.status = "Preparando extracción (sin guardar aún)…"

        try:
            async with self:
                auth = await self.get_state(AuthState)
                usuario_id = int(auth.usuario_id or 0)
            propietario = None if mode == "admin" else usuario_id

            ok_db = await asyncio.to_thread(sb.bootstrap_database)
            if not ok_db:
                async with self:
                    self.error = "Ingesta requiere Neon + OpenRouter."
                    self.status = ""
                    self.loading = False
                    self._job_running = False
                return

            materias_opts: list[str] = []
            try:
                mats = await asyncio.to_thread(lambda: sb.banco_repo().fetch_materias())
                materias_opts = [str(m.get("nombre") or "") for m in mats if m.get("nombre")]
            except Exception:
                materias_opts = []

            collected: list[dict[str, Any]] = list(existing)
            warnings: list[str] = []
            n = len(paths) or 1

            for i, path_str in enumerate(paths):
                meta = meta_list[i] if i < len(meta_list) else {}
                name = str(meta.get("name") or Path(path_str).name)
                kind = str(meta.get("kind") or "")
                file_pages = int(meta.get("pages") or 0)

                async with self:
                    self.progress_pct = int((i / n) * 85) + 5
                    self.progress_detail = (
                        f"Archivo {i + 1}/{n}: densidad adaptativa · {name}"
                    )
                    self.status = self.progress_detail
                    self.uploaded_name = name

                bundle = await asyncio.to_thread(
                    IngestState._process_one_file_sync,
                    path_str=path_str,
                    name=name,
                    kind=kind,
                    file_pages=file_pages,
                    ranges=ranges if kind == "pdf" else [{"start": 1, "end": 1}],
                    propietario=propietario,
                )
                if not bundle.get("ok"):
                    w = str(bundle.get("warning") or "")
                    if w:
                        warnings.append(w)
                else:
                    warnings.extend(list(bundle.get("warnings") or []))
                    for it in bundle.get("items") or []:
                        collected.append(dict(it))

                async with self:
                    self.progress_pct = int(((i + 1) / n) * 90)
                    self.progress_detail = (
                        f"Archivo {i + 1}/{n} · {len(collected)} ítems en revisión"
                    )

            # Asignar UIDs estables
            async with self:
                seq = int(self._item_seq)
                normalized = []
                for it in collected:
                    if not it.get("uid"):
                        seq += 1
                        it = dict(it)
                        it["uid"] = f"q{seq}"
                    alts = it.get("alternativas") or {}
                    if isinstance(alts, dict):
                        it.setdefault("alt_a", alts.get("A", ""))
                        it.setdefault("alt_b", alts.get("B", ""))
                        it.setdefault("alt_c", alts.get("C", ""))
                        it.setdefault("alt_d", alts.get("D", ""))
                        it.setdefault("alt_e", alts.get("E", ""))
                    it.setdefault("tag_tematico", it.get("tema_especifico", ""))
                    it.setdefault("nivel_estimado", "intermedia")
                    it.setdefault("rango_origen", "")
                    normalized.append(it)
                self._item_seq = seq
                self.pending_items = normalized
                self.materias_options = materias_opts
                self.last_warnings = " · ".join(warnings[:8])
                self.progress_pct = 100
                self.progress_detail = "Listo para revisión"
                self.status = (
                    f"¡Extracción lista! {len(normalized)} pregunta(s) en revisión "
                    "(aún NO guardadas en tu bóveda)."
                )
                self.stage = "review"
                self.loading = False
                self._job_running = False
                # Evita re-analizar el mismo rango por accidente; el PDF sigue activo.
                self.page_ranges = []
                # Recomputar resumen
                counts: dict[str, int] = {}
                for it in normalized:
                    mat = str(it.get("materia_nombre") or "Sin materia")
                    counts[mat] = counts.get(mat, 0) + 1
                parts = [
                    f"{v} {k}"
                    for k, v in sorted(counts.items(), key=lambda x: (-x[1], x[0]))
                ]
                self.area_summary = (
                    "Distribución por área: " + ", ".join(parts)
                    if parts
                    else "Sin ítems."
                )
        except Exception as exc:
            async with self:
                self.error = f"Ingesta falló: {exc}"
                self.status = ""
                self.progress_detail = "Error"
                self.loading = False
                self._job_running = False

    @rx.event
    def delete_pending_item(self, uid: str):
        self.pending_items = [
            it for it in self.pending_items if str(it.get("uid")) != str(uid)
        ]
        self._recompute_area_summary()
        if self.editing_uid == uid:
            self.editing_uid = ""

    @rx.event
    def start_edit_item(self, uid: str):
        item = next((it for it in self.pending_items if str(it.get("uid")) == str(uid)), None)
        if not item:
            return
        self.editing_uid = str(uid)
        self.edit_enunciado = str(item.get("enunciado") or "")
        self.edit_justificacion = str(item.get("justificacion") or "")
        self.edit_materia = str(item.get("materia_nombre") or "")
        self.edit_tema = str(item.get("tema_especifico") or "")
        self.edit_tag = str(item.get("tag_tematico") or "")
        self.edit_nivel = str(item.get("nivel_estimado") or "intermedia")
        self.edit_correcta = str(item.get("alternativa_correcta") or "A")
        alts = item.get("alternativas") or {}
        self.edit_a = str(alts.get("A") or item.get("alt_a") or "")
        self.edit_b = str(alts.get("B") or item.get("alt_b") or "")
        self.edit_c = str(alts.get("C") or item.get("alt_c") or "")
        self.edit_d = str(alts.get("D") or item.get("alt_d") or "")
        self.edit_e = str(alts.get("E") or item.get("alt_e") or "")

    @rx.event
    def cancel_edit(self):
        self.editing_uid = ""

    @rx.event
    def set_edit_enunciado(self, v: str):
        self.edit_enunciado = v

    @rx.event
    def set_edit_justificacion(self, v: str):
        self.edit_justificacion = v

    @rx.event
    def set_edit_materia(self, v: str):
        self.edit_materia = v

    @rx.event
    def set_edit_tema(self, v: str):
        self.edit_tema = v

    @rx.event
    def set_edit_tag(self, v: str):
        self.edit_tag = v

    @rx.event
    def set_edit_nivel(self, v: str):
        self.edit_nivel = v

    @rx.event
    def set_edit_correcta(self, v: str):
        self.edit_correcta = str(v).strip().upper()[:1] or "A"

    @rx.event
    def set_edit_a(self, v: str):
        self.edit_a = v

    @rx.event
    def set_edit_b(self, v: str):
        self.edit_b = v

    @rx.event
    def set_edit_c(self, v: str):
        self.edit_c = v

    @rx.event
    def set_edit_d(self, v: str):
        self.edit_d = v

    @rx.event
    def set_edit_e(self, v: str):
        self.edit_e = v

    @rx.event
    def save_edit_item(self):
        uid = self.editing_uid
        if not uid:
            return
        updated = []
        for it in self.pending_items:
            if str(it.get("uid")) != uid:
                updated.append(it)
                continue
            row = dict(it)
            row["enunciado"] = self.edit_enunciado.strip()
            row["justificacion"] = self.edit_justificacion.strip()
            row["materia_nombre"] = self.edit_materia.strip()
            row["tema_especifico"] = self.edit_tema.strip()
            row["tag_tematico"] = self.edit_tag.strip() or self.edit_tema.strip()
            row["nivel_estimado"] = self.edit_nivel
            row["alternativa_correcta"] = self.edit_correcta
            row["alternativas"] = {
                "A": self.edit_a,
                "B": self.edit_b,
                "C": self.edit_c,
                "D": self.edit_d,
                "E": self.edit_e,
            }
            row["alt_a"] = self.edit_a
            row["alt_b"] = self.edit_b
            row["alt_c"] = self.edit_c
            row["alt_d"] = self.edit_d
            row["alt_e"] = self.edit_e
            updated.append(row)
        self.pending_items = updated
        self.editing_uid = ""
        self._recompute_area_summary()
        self.status = "Ítem actualizado (aún pendiente de guardar en bóveda)."

    @rx.event
    def reclassify_item(self, uid: str, materia: str):
        updated = []
        for it in self.pending_items:
            if str(it.get("uid")) == str(uid):
                row = dict(it)
                row["materia_nombre"] = materia
                updated.append(row)
            else:
                updated.append(it)
        self.pending_items = updated
        self._recompute_area_summary()

    @rx.event
    async def confirm_save_items(self):
        """Persiste en Neon solo los ítems aceptados en revisión."""
        if not self.pending_items:
            self.error = "No hay ítems para guardar."
            return
        self.loading = True
        self.error = ""
        self.status = "Guardando en tu bóveda…"
        yield
        auth = await self.get_state(AuthState)
        try:
            if not sb.bootstrap_database():
                self.error = "Sin Neon: no se pudo guardar."
                return
            propietario = None if self.mode == "admin" else int(auth.usuario_id)
            payload = []
            for it in self.pending_items:
                payload.append(
                    {
                        "materia_nombre": it.get("materia_nombre"),
                        "tema_especifico": it.get("tema_especifico"),
                        "enunciado": it.get("enunciado"),
                        "alternativas": it.get("alternativas")
                        or {
                            "A": it.get("alt_a"),
                            "B": it.get("alt_b"),
                            "C": it.get("alt_c"),
                            "D": it.get("alt_d"),
                            "E": it.get("alt_e"),
                        },
                        "alternativa_correcta": it.get("alternativa_correcta"),
                        "justificacion": it.get("justificacion"),
                    }
                )
            pers = sb.banco_repo().persist_items_transactional(
                items=payload,
                materia_id=None,
                origen_contenido="pdf" if self.has_pdf else "imagen",
                fuente="openrouter",
                nombre_archivo_fuente=self.uploaded_name or None,
                propietario_usuario_id=propietario,
            )
            self.last_insertadas = int(pers.get("n_insertadas") or 0)
            self.last_duplicadas = int(pers.get("n_duplicadas") or 0)
            self.status = (
                f"¡Guardado! Insertadas: {self.last_insertadas} · "
                f"Duplicadas: {self.last_duplicadas}"
            )
            self.stage = "done"
            self.pending_items = []
            self.area_summary = ""
        except Exception as exc:
            self.error = f"No se pudo guardar: {exc}"
        finally:
            self.loading = False




class DashboardState(rx.State):
    loaded: bool = False
    empty: bool = True
    error: str = ""
    indice: float = 0.0
    estado: str = ""
    frase: str = ""
    total_intentos: int = 0
    precision_pct: float = 0.0
    mision: str = ""
    cuello: str = ""
    materias_resumen: list[dict[str, Any]] = []

    def clear(self):
        self.loaded = False
        self.empty = True
        self.error = ""
        self.indice = 0.0
        self.estado = ""
        self.frase = ""
        self.total_intentos = 0
        self.precision_pct = 0.0
        self.mision = ""
        self.cuello = ""
        self.materias_resumen = []

    @rx.event
    def load(self, usuario_id: int = 0):
        self.error = ""
        if not usuario_id:
            self.clear()
            return
        if not sb.bootstrap_database():
            self.error = "Dashboard requiere Neon."
            self.loaded = True
            self.empty = True
            return
        try:
            from app.services.student_dashboard_service import (
                calcular_indice_medicina,
                construir_plan_semanal,
                enriquecer_resumen_con_dominio,
                resumen_por_materia,
            )

            kpis = sb.historial_repo().fetch_kpis_alumno(int(usuario_id))
            por_tema = sb.historial_repo().fetch_rendimiento_por_tema(int(usuario_id))
            ventanas = sb.historial_repo().fetch_precision_ventanas(
                int(usuario_id), dias=7
            )
            total = int(kpis.get("total_intentos") or 0)
            self.total_intentos = total
            self.precision_pct = float(kpis.get("precision_pct") or 0)
            if total == 0:
                self.empty = True
                self.loaded = True
                self.mision = (
                    "Completa una práctica o simulacro para armar tu Índice Medicina."
                )
                return
            indice = calcular_indice_medicina(
                precision_pct=self.precision_pct,
                total_intentos=total,
                precision_7d=ventanas.get("precision_7d"),
                precision_7d_prev=ventanas.get("precision_7d_prev"),
            )
            plan = construir_plan_semanal(por_tema, min_intentos=3)
            materias = enriquecer_resumen_con_dominio(resumen_por_materia(por_tema))
            self.indice = float(indice.get("indice") or 0)
            self.estado = str(indice.get("estado") or "")
            self.frase = str(indice.get("frase_pronostico") or "")
            self.mision = str(plan.get("mision") or "")
            cuello = plan.get("cuello_botella") or {}
            self.cuello = str(
                cuello.get("tema_nombre") or cuello.get("nombre") or ""
            )
            # Normalizar materias a dicts simples (+ Nivel de Dominio)
            clean = []
            if isinstance(materias, list):
                for m in materias[:12]:
                    if isinstance(m, dict):
                        clean.append(
                            {
                                "nombre": str(m.get("materia_nombre") or ""),
                                "precision": float(m.get("precision_pct") or 0),
                                "intentos": int(m.get("n_intentos") or 0),
                                "dominio": str(m.get("dominio_etiqueta") or ""),
                                "dominio_score": float(m.get("dominio_score") or 0),
                            }
                        )
            self.materias_resumen = clean
            self.empty = False
            self.loaded = True
        except Exception as exc:
            self.error = str(exc)
            self.loaded = True
