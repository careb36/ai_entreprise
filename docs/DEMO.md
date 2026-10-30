# Guion del vídeo de demo

Duración objetivo: **2 min 30 s**. Tope de los hackathons: 3 min. Si hay que
recortar, se quita la escena 5, nunca la 3 ni la 4.

La regla que ordena todo: **cada escena enseña una cosa que el jurado puede
comprobar en pantalla**, no una diapositiva de arquitectura. El producto no es
"agentes que escriben código"; es la capa que **verifica** lo que dicen y
**registra la evidencia**. El vídeo tiene que verse como eso.

## Antes de grabar (5 minutos)

```bash
docker compose up -d                       # backend :8000, frontend :5173, proxy :5100
curl -s localhost:8000/health              # {"status":"healthy",...,"nats":"connected"}
curl -s localhost:5173/ -o /dev/null -w '%{http_code}\n'   # 200
PROXY_URL=http://localhost:5100 ./scripts/verify-nebius.sh # 3 capas OK
```

Comprobado en este árbol: los tres comandos pasan. El proxy publica **47
modelos** (6 de ellos de Nebius Token Factory).

El arranque del proxy en una imagen limpia se arregló en el PR #256, mergeado el
2026-10-29: si `llm-proxy` no levanta, mira si tu copia es anterior a ese merge.

---

## Escena 1 — El problema (0:00–0:20)

**Plano:** cara o diapositiva negra con texto. Sin música épica.

> "Un agente escribe código y te dice que ha terminado. ¿Le crees?
> Nosotros construimos la parte que no consiste en creerle."

Corte seco. Nada de "hola, soy…".

## Escena 2 — Qué es (0:20–0:40)

**Plano:** pantalla en la portada del frontend, `localhost:5173`.

> "La Fabrique es una fábrica de software: se le da una empresa y un objetivo,
> planifica un sprint, lo ejecuta con agentes, y **cada afirmación que hacen
> pasa por un verificador que no es un modelo de lenguaje**."

**A grabar:** el dashboard con un sprint real. Que se vean tareas en columnas
distintas, no todas en verde.

## Escena 3 — La evidencia (0:40–1:15) ← **la escena que importa**

**Plano:** `/evidence`, y abrir un run concreto (`/evidence/:runId`).

Aquí se ve lo único que ningún otro proyecto del hackathon va a tener:

> "Fíjate en que aquí hay **dos columnas que nunca se mezclan**. A la izquierda,
> lo que el agente **afirma** que hizo. A la derecha, lo que el daemon
> **observó** en disco. Son campos distintos a propósito: un claim y una
> observación no pueden vivir en el mismo sitio, porque entonces nadie sabe
> cuál de los dos está leyendo."

Señalar en pantalla, despacio:

1. `claims.files` — lo que el agente dijo.
2. `workspace_writes` — los ficheros que el daemon vio escribir.
3. El veredicto `verified` / `refuted` / **`unverifiable`**.

> "`unverifiable` es un veredicto propio: **no saber no es aprobar**."

Y el detalle que demuestra que el sistema es honesto consigo mismo:

> "Ni el hash ni la firma se los pedimos a la parte que estamos grabando.
> Si se los pidiéramos a ella, no probarían nada."

**Si no hay runs sembrados**, la escena 3 se graba con la respuesta de la API,
que es igual de contundente:

```bash
curl -s "localhost:8000/v1/runs?company_id=1" | jq '.[0] | {claims, observation, verdict}'
```

## Escena 4 — Nebius / Nemotron, en vivo (1:15–1:45)

**Plano:** terminal.

> "Los agentes no llaman a un proveedor directamente: todo pasa por un proxy que
> lleva presupuesto por empresa. Y entre los proveedores está **Nebius Token
> Factory** con los Nemotron de NVIDIA."

```bash
./scripts/verify-nebius.sh
```

Se graba la salida real (esta salida es de este árbol, no inventada):

```
== 1. Token Factory directo ==       modelos servidos: 30
   nemotron: nvidia/nemotron-3-super-120b-a12b
   nemotron: nvidia/Nemotron-3-Ultra-550b-a55b
== 2. Catálogo del proxy ==          modelos nebius publicados: 6
== 3. Completion real vía proxy ==   provider: nebius | tokens: 44
   OK: las tres capas responden
```

> "Tres capas comprobadas por separado, para que si algo falla se sepa **cuál**
> está roto. Y el catálogo no está escrito a mano: se pide a Token Factory, así
> que un Nemotron nuevo aparece sin tocar código."

## Escena 5 — El bucle completo (1:45–2:15)

**Plano:** terminal, con el e2e ya lanzado en otra pestaña.

> "Esto no es una demo de diapositivas. Hay un test de extremo a extremo que
> levanta Postgres, NATS, el backend, el daemon y un modelo falso, y conduce una
> empresa por sprints enteros."

Si da tiempo, se graba el final:

```
$ ./scripts/e2e/run.sh
...
57 ok, 0 FAIL
E2E PASSED
```

> "Cincuenta y siete comprobaciones, ninguna del propio sistema sobre sí mismo:
> el historial de cada tarea lo lee un módulo aparte que **no comparte código**
> con los lectores que comprueba."

## Escena 6 — Cierre (2:15–2:30)

**Plano:** el repositorio en GitHub, `main`, con los checks en verde.

> "Todo lo que has visto está en el repo, con la licencia Apache 2.0.
> Si un agente te dice que ha terminado, esto es lo que hace falta para
> poder creérselo."

---

## Lo que NO se dice en el vídeo

- No decir "multi-agente" como si fuera el logro. El logro es la verificación.
- No prometer que el sistema es autónomo. Es autónomo **hasta** donde una
  persona decide: una tarea de riesgo alto espera a un humano en cualquier
  nivel de autonomía. Eso es una función, no una limitación.
- No enseñar `mypy` ni los 46 errores de baseline. En el vídeo no aporta y
  distrae.

## Plan B si algo no arranca

| Si falla | Qué hacer |
|---|---|
| `llm-proxy` no arranca | aplicar el `Dockerfile` del PR #256 |
| Frontend sin datos | grabar escenas 3 y 4 contra la API con `curl`/`jq` |
| El e2e tarda | grabar el `E2E PASSED` ya obtenido y decir que es una ejecución real |
| Sin red para Nebius | escena 4 con el catálogo local de `/v1/models` (47 modelos) |

Grabar siempre: **pantalla completa, 1080p, cursor grande, sin notificaciones**.
Cada escena de una sola toma; si sale mal, se repite entera.
