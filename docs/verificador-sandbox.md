# Sandbox del verificador

Plan para que las observaciones que juzga el verificador las produzca el daemon
de verdad, sobre bytes que están en un disco de verdad, en lugar de literales
escritos por la misma persona que escribió la comprobación.

Estado: plan. No hay código todavía.

## Por qué

`backend/eval/verificador/` mide una propiedad concreta: de las entregas cuyas
afirmaciones la evidencia contradice o no puede sostener, cuántas se niega a
creer el verificador. Hoy son 14 casos, 12 de ellos mentiras, y el número sale
del `verify_agent_run` real, sin mocks. Eso está bien y no se toca.

El problema es de dónde sale la evidencia. En los 14 casos, los dos lados de
cada comparación los escribió el mismo archivo:

- `SENT_SHA = "a" * 64`. Nadie ha hasheado nada. `evaluate_workspace_writes`
  compara dos constantes, así que prueba que la comparación está cableada, nunca
  que el sha del daemon corresponda a los bytes que envió el backend.
- `artifact_text = API_DIFF`, un diff literal. `claimed_files_present` hace
  búsqueda de subcadenas contra una cadena que escribió el autor de la propia
  comprobación.
- `_writes(status="refused")`. El daemon emite `written`, `skipped_protected`,
  `rejected` y `workspace_missing`. **`refused` no existe.** El caso pasa, pero
  no por lo que dice: no puede fallar si el verificador maltrata un estado real,
  porque el estado que le dan no es real.
- `test_run` literal. En una ejecución real de pytest, el mensaje de la
  aserción que falló suele caer **fuera** del tail de 2000 caracteres
  (`TEST_OUTPUT_TAIL_CHARS`). El banco no puede enseñar si la observación del
  verificador sigue siendo honesta al recortar.

Un fixture comparte los puntos ciegos de quien lo escribió; eso es justo lo que
un fixture no puede detectar. La propiedad que vende el producto es que las
observaciones vienen de otro sitio. Mientras las escribamos nosotros, el número
mide nuestra imaginación, no el producto.

Esto no es teórico: en #273 el verificador recortaba en silencio la salida de
los tests y lo leía como prueba completa. Un caso con el tail escrito a mano no
lo habría encontrado, porque el que escribió el caso creía que el fallo era
visible.

## Qué prueba el sandbox

Que el verificador llega al mismo veredicto cuando la evidencia la produce el
daemon real —bytes en disco, un subproceso real, un cuelgue real— en vez de un
literal. Ni una comprobación nueva, ni un verificador nuevo: la misma función,
con la entrada de verdad.

## Diseño: tres capas, sin infraestructura nueva

**Capa 1 — workspace efímero.** `tmp_path` de pytest. Un directorio real. Nada
más: ni Docker, ni NATS, ni Postgres. `verify_agent_run(session, run, evidence)`
recibe un dict de evidencia y la sesión; `sqlite_session` ya existe en
`tests/conftest.py`.

**Capa 2 — el daemon real.** Importar `write_files` / `deliver_files` de
`vscode-daemon/daemon.py` y `execute_shell_command` de
`vscode-daemon/shell_exec.py`. Los dos ya son funciones puras de
`(workspace_path, files)` y `(command, cwd)`. `daemon.py` hace imports planos
(`import nats_scope`, `import shell_exec`), así que el fixture añade
`vscode-daemon/` a `sys.path`; `nats` ya está instalado en el venv del backend,
así que el import funciona sin tocar `pyproject.toml`.

El sandbox compone la evidencia como la compone producción:

| Campo de `evidence` | Quién lo produce en el sandbox |
|---|---|
| `expected[path]` | el lado backend: sha256 de los bytes que envió, igual que en `services/workspace_daemon.py` |
| `workspace_writes` | `write_files(...)`, que hashea lo que leyó del disco después de escribir |
| `test_run` y `ci` | `record_test_run(evidence, command, execute_shell_command(...))`, el traductor real |

Ese último renglón importa: al usar `record_test_run` se prueba también la
costura, no sólo el verificador. Un cambio en cómo se traduce una respuesta del
daemon a `test_run` rompe el sandbox.

**Capa 3 — el verificador real.** `verify_agent_run`, sin cambios.

Así el sandbox es un cableado, no un entorno nuevo. De ahí que quepa antes del
deadline.

## Casos que habilita

| Caso | Evidencia real que lo produce | Por qué el literal no puede |
|---|---|---|
| sha de verdad | `write_files` hashea lo leído; `expected` se calcula sobre lo enviado | dos constantes siempre coinciden o nunca: no prueba que el digest sea del contenido |
| contenido cambiado en disco | escribir, mutar el archivo, después pedir el informe | el literal dice "distinto", no que el verificador mire el disco |
| `skipped_protected` | `write_files` con un `README.md` que ya existe | `refused` no existe: si el verificador ignorase un estado real, el caso no se enteraría |
| `workspace_missing` | `write_files("/ruta/que/no/existe", ...)` | ídem. Es el "el proyecto no está en esta máquina", real en multi-host |
| `rejected` por ruta insegura | `write_files` con `../../etc/passwd` | prueba a la vez que el sandbox no escapa del tempdir y que el verificador lo ve |
| tests que fallan de verdad | `execute_shell_command("... raise SystemExit(1)")` | el código de salida lo produce un proceso, no una constante |
| tests que cuelgan | `COMMAND_TIMEOUT_SECONDS` monkeypatcheado a 0.2s y `sleep 5` | el `subprocess.TimeoutExpired` es real, y tarda 0.2s |
| salida truncada | comando que imprime >2000 caracteres con el fallo en medio | con literal no se sabe si el fallo cae dentro del tail |
| diff real | `git diff` sobre un repo temporal | un rename, un borrado o un binario no se parecen a un diff escrito a mano |

Los tres estados reales (`skipped_protected`, `workspace_missing`, `rejected`)
son los que hoy no se ejercitan con su nombre verdadero, y son los que un
producer puede provocar sin querer.

## Cómo se evita que el sandbox se auto-certifique

Un sandbox que calcula los dos lados de una comparación sólo puede demostrar
que son iguales. La misma disciplina que ya usa el banco con `targets`: cada
mutación tiene que poner la suite en rojo, y se comprueba una por una.

1. `write_files` devuelve un sha falseado → `files_written` debe refutar.
2. `execute_shell_command` devuelve `exit_code: 0` sobre un test que falla →
   `tests_run` debe refutar.
3. El daemon omite una entrada de `files` → `files_written` debe refutar con
   "not reported".

Y el control: una entrega honesta (escritura real, test real en verde) vuelve
`verified`. Sin ese control, un sandbox que rechaza todo saca 100%.

## Determinismo y coste

- Sin red, sin Docker, sin Postgres: `sqlite_session` + `tmp_path`.
- Nada de rutas absolutas ni de tails completos en las aserciones: el texto de
  la salida cambia entre máquinas. Se afirma el veredicto y la presencia de una
  subcadena, nunca el texto entero.
- El cuelgue se prueba bajando la constante, no esperando 120 segundos.
- Objetivo: el sandbox entero por debajo de 3 segundos.
- `git` sólo en el caso del diff, y se salta con `shutil.which("git")` si no
  está.

## Dónde vive

- `backend/tests/test_verificador_sandbox.py` — corre en la suite normal, que
  CI ya ejecuta, así que una regresión rompe el build.
- `backend/eval/verificador/sandbox.py` — los casos, reutilizables por quien
  consuma el banco en Python puro (el informe de evidencia), sin arrastrar
  pytest.
- `cases.py` se queda: la matriz sintética es barata y cubre combinaciones que
  el sandbox no necesita repetir. El sandbox es la prueba de que los casos
  sintéticos se parecen a la realidad.

## Límites, dichos

- El sandbox corre el daemon en **esta** máquina. No prueba el comportamiento
  en un host Windows (separadores en `_resolve_target_path`,
  `build_shell_command`): eso sigue cubierto sólo por tests unitarios.
- No sustituye al e2e (`scripts/e2e/run.sh`), que prueba el circuito con
  Postgres y NATS. El sandbox prueba la verificación, no el transporte.
- No mide el riesgo de que el daemon mienta. Eso es otra frontera, y hoy está
  fuera del alcance del verificador por diseño.

## Orden de trabajo

1. Fixture `workspace`: `tmp_path`, `sys.path`, helpers que componen la
   evidencia. Unas 40 líneas.
2. Las cuatro primeras filas de la tabla: sha real, contenido cambiado,
   `skipped_protected`, `workspace_missing`.
3. `test_run` real: fallo, éxito y truncado.
4. Cuelgue con la constante monkeypatcheada.
5. Las tres mutaciones (cada una vista en rojo) y el control honesto.
6. Diff real con git. Última, porque es la que más depende del entorno.

Sin dependencias nuevas.
