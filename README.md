# Rutas Aéreas Argentinas — Mapa interactivo

App Flask + Postgres que muestra un mapa mundial interactivo de rutas aéreas
argentinas (cabotaje e internacional) con vuelos, pasajeros y ocupación por
ruta, a partir de las planillas "series históricas" de ANAC.

## Uso

- `/` — el mapa público.
- `/admin` — panel para subir nuevos Excel de ANAC (protegido por contraseña,
  variable de entorno `ADMIN_PASSWORD`).

## Deploy en Render

1. Subí este repo a GitHub.
2. En Render: **New + → Blueprint**, elegí este repo. `render.yaml` crea
   automáticamente la base Postgres y el servicio web.
3. Una vez desplegado, andá a la pestaña **Environment** del servicio web y
   anotá el valor generado de `ADMIN_PASSWORD` (Render lo genera solo por
   seguridad) — con eso entrás a `/admin`. Podés cambiarlo ahí mismo.
4. Entrá a `/admin`, subí los `.xlsx` de ANAC (hoja "OUT"), y listo — el mapa
   se actualiza solo.

## Actualizar datos más adelante

Cada vez que ANAC publique una planilla nueva, subila desde `/admin`. Los
meses/años que ya existen se actualizan (se pisan), los nuevos se agregan.
Nada se borra ni se duplica.
