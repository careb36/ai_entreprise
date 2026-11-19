"""The Spanish fixed strings of the domain checks (``domain_checks``, issue
#692): our own wording; the same keys and placeholders as ``texts_checks_en``."""

TEXTS: dict[str, str] = {
    "kind.quota": "Cuota de la unidad de negocio",
    "check.basis_not_read": "no se leyeron los registros que necesita",
    "check.approved_first": "El trabajo empieza sobre un plan aprobado",
    "check.approved_first_how": "los sprints planificados desde una especificación, su primer evento de tarea frente a la aprobación de esa versión de la especificación",
    "check.approved_first_obs": "{part} de {whole} sprints planificados desde una especificación empezaron después de su aprobación; {unplanned} no se planificaron desde una",
    "check.approved_first_none": "no empezó ningún sprint planificado desde una especificación",
    "check.approved_first_unknown": "la versión de la especificación de {part} de {whole} sprints empezados no está en los registros",
    "check.churn": "El alcance de un sprint se mantiene una vez planificado",
    "check.churn_how": "las tareas que se sumaron a un sprint después de planificarlo, aceptadas desde una solicitud de entrada (el evento de tarea de la plataforma), sobre las tareas de los sprints",
    "check.churn_obs": "{part} de {whole} tareas se sumaron a su sprint después de planificarlo, desde una solicitud de entrada",
    "check.on_time": "Las iteraciones terminan en su fin planificado",
    "check.on_time_how": "los sprints completados cuyo último evento de tarea cae el último día de su fecha de inicio más su duración planificada en días, o antes",
    "check.on_time_obs": "{part} de {whole} sprints completados con duración planificada cerraron en su fin planificado o antes; {unplanned} no tienen duración planificada",
    "check.on_time_none": "ningún sprint completado tiene registrada una duración planificada (fecha de inicio y días)",
    "check.label_length": "duración planificada de {days} días",
    "check.quota": "El uso de modelos se mantiene dentro de la cuota configurada",
    "check.quota_how": "los tokens contados contra la cuota de tokens configurada de las unidades de negocio en este período, tal como los informó el proxy de modelos",
    "check.quota_obs": "{used} de {limit} tokens de la cuota configurada usados en este período ({units} unidades de negocio con cuota; {reached} la alcanzaron)",
    "check.quota_none": "ninguna unidad de negocio tiene configurada una cuota de tokens (0 significa sin límite)",
    "check.sprint_team": "El trabajo de cada sprint va al equipo elegido para él",
    "check.sprint_team_how": "las tareas de los sprints que registran un equipo, asignadas a un rol de ese equipo",
    "check.sprint_team_obs": "{part} de {whole} tareas de sprints con equipo van a un rol de ese equipo",
    "check.sprint_team_none": "ningún sprint registra un equipo",
}
