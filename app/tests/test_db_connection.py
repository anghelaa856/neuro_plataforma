"""Prueba básica: usuarios + PostgreSQL + tarjetas por usuario_id."""

from app.database.db_manager import db_manager


def main() -> None:
    db_manager.connect()
    db_manager.ensure_schema()

    email = "prueba_conexion@example.com"
    try:
        user = db_manager.register_user(
            email=email,
            password="secreto123",
            nombre="Usuario Prueba",
        )
    except ValueError:
        user = db_manager.authenticate_user(email=email, password="secreto123")
        assert user is not None, "No se pudo autenticar usuario de prueba"

    usuario_id = int(user["id_usuario"])
    print(f"OK usuario id={usuario_id}")

    card_id = db_manager.insert_memory_card(
        usuario_id=usuario_id,
        area="Prueba",
        tema="Conexion Inicial",
        pregunta="¿2+2?",
        respuesta_referencia="4",
        respuesta_estudiante="4",
        nota_ia=5.0,
        auditoria_estado="Normal",
        auditoria_tiempo_ms=1000,
        intervalo_recomendado_dias=1,
        plan_estudio="retencion",
        tipo_pregunta="open",
        modo_simulacro=False,
        origen_contenido="manual",
        repetitions_count=0,
        easiness_factor=2.5,
    )
    print(f"OK insert id={card_id}")

    rows = db_manager.fetch_memory_cards(usuario_id=usuario_id, limit=5)
    assert any(r.get("tema") == "Conexion Inicial" for r in rows), "No se leyó lo insertado"
    print(f"OK fetch ({len(rows)} filas recientes)")

    dash = db_manager.fetch_progress_dashboard(usuario_id=usuario_id)
    print("OK dashboard keys:", list(dash.keys()) if isinstance(dash, dict) else type(dash))
    print("PRUEBA DB SUPERADA")


if __name__ == "__main__":
    main()
