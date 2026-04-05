# Contributing to La Fabrique

Este proyecto sigue estándares de ingeniería de alta calidad para garantizar la mantenibilidad y la colaboración. 

---

## 🌿 Estrategia de Ramas: GitFlow

Seguimos el modelo GitFlow para el ciclo de vida del software.

### Ramas Principales
- `main`: Código estable y listo para producción.
- `develop`: Rama de integración para el desarrollo en curso.

### Ramas de Trabajo
| Tipo | Prefijo | Origen | Destino | Ejemplo |
| :--- | :--- | :--- | :--- | :--- |
| Feature | `feature/` | `develop` | `develop` | `feature/auth-sqlmodel` |
| Bugfix | `bugfix/` | `develop` | `develop` | `bugfix/nats-retry-logic` |
| Release | `release/` | `develop` | `main` & `develop` | `release/v2.1.0` |
| Hotfix | `hotfix/` | `main` | `main` & `develop` | `hotfix/critical-db-fix` |

---

## 📝 Convención de Commits: Conventional Commits

Todos los commits deben seguir el formato: `<type>(<scope>): <description>`

### Tipos Permitidos
- `feat`: Nueva funcionalidad.
- `fix`: Corrección de un error.
- `docs`: Cambios solo en la documentación.
- `style`: Cambios de formato (espacios, comas) sin afectar la lógica.
- `refactor`: Cambio de código que no corrige un error ni añade una funcionalidad.
- `perf`: Cambio de código que mejora el rendimiento.
- `test`: Añadir o corregir tests.
- `chore`: Cambios en el proceso de construcción o herramientas auxiliares.
- `ci`: Cambios en los archivos y scripts de configuración de CI.

### Ejemplos
- `feat(auth): implementar registro con SQLModel`
- `fix(nats): corregir reconexión exponencial en el cliente`
- `refactor(backend): modularizar endpoints de sprint en routers independientes`

---

## 🚀 Flujo de Trabajo para IA (Copilot / Agents)

1. **Investigar:** Leer el `REFACTORING_PLAN.md` para entender el objetivo actual.
2. **Rama:** Crear una rama `feature/` o `bugfix/` desde `develop`.
3. **Implementar:** Escribir código siguiendo los patrones de diseño (Screaming Architecture).
4. **Validar:** Asegurarse de que el código compile y pase los linters.
5. **Commit:** Realizar commits atómicos y descriptivos siguiendo la convención.
6. **PR:** Describir los cambios realizados en el Pull Request.
