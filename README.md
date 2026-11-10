# ✈️ Tracker de vuelos Medellín ⇄ Bogotá

Revisa **dos veces al día** (≈ 6:41 a.m. y 6:41 p.m. hora Colombia) los precios para:

- **Ida:** 20 de marzo de 2027 · MDE (Rionegro) / EOH (Olaya Herrera) → BOG
- **Vuelta:** 23 de marzo de 2027 · BOG → MDE / EOH

Agrupa los vuelos en **franjas horarias** (madrugada, mañana, mediodía, tarde, noche), guarda cada
consulta en un histórico y publica un dashboard con el más barato de cada franja, el mínimo histórico
y la evolución día a día.

📊 **Dashboard:** https://santiagozu.github.io/vuelos-mde-bog/
📝 **Último reporte en texto:** [`reports/ultimo.md`](reports/ultimo.md)

## Fuentes

| Fuente | Qué trae | Costo |
|---|---|---|
| **Google Flights** (librería [`faster-flights`](https://pypi.org/project/faster-flights/)) | Tarifas de las aerolíneas: Avianca, LATAM, JetSMART, Wingo, Clic (si Google la tiene indexada) | Gratis, sin API key |
| **SerpApi** (opcional) | Quién vende cada vuelo: la aerolínea directa **y agencias** (Despegar, Expedia, Kiwi…) | Gratis hasta 250 búsquedas/mes |

No se hace scraping directo de avianca.com / latamairlines.com: tienen anti‑bots fuertes y desde
GitHub Actions se rompería a diario. Google Flights ya muestra las tarifas que publican las propias aerolíneas.

## Configuración (una sola vez)

1. **GitHub Pages:** Settings → Pages → *Build and deployment* → Source: **Deploy from a branch** →
   Branch: `main`, carpeta **`/docs`** → Save.
2. **Agencias (opcional):** crea una cuenta gratis en [serpapi.com](https://serpapi.com/users/sign_up),
   copia tu API key y guárdala en Settings → Secrets and variables → Actions → *New repository secret*
   con el nombre `SERPAPI_KEY`.
3. **Alertas por Telegram (opcional):** crea un bot con [@BotFather](https://t.me/BotFather), escríbele
   un mensaje y obtén tu chat id (p. ej. con `https://api.telegram.org/bot<TOKEN>/getUpdates`). Guarda
   los secrets `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID`. Te avisa cuando ida + vuelta marca **nuevo mínimo histórico**.
4. Primera ejecución: pestaña **Actions → Rastrear vuelos MDE-BOG → Run workflow**.

## Cambiar parámetros

Todo está en [`config.yaml`](config.yaml): fechas, aeropuertos, número de pasajeros, franjas horarias,
solo vuelos directos, fuentes y alertas. El horario del cron está en
[`.github/workflows/track.yml`](.github/workflows/track.yml).

## Cómo funciona

```
cron (2x/día) ─▶ tests ─▶ python -m tracker.main run
                           ├─ Google Flights: 1 búsqueda por par de aeropuertos y tramo
                           ├─ SerpApi (máx 1 vez/día): 1 búsqueda por tramo + top N "booking options"
                           ├─ append ─▶ data/observaciones.csv   (histórico, 1 fila por precio visto)
                           ├─ agrega  ─▶ docs/data.json  (dashboard)  +  reports/ultimo.md
                           └─ alerta Telegram si hay nuevo mínimo
                         ─▶ commit al repo ─▶ GitHub Pages se actualiza solo
```

El pipeline deja de consultar automáticamente después del 20 de marzo de 2027.

## Correr localmente

```bash
pip install -r requirements.txt
pytest -q                          # pruebas sin red
python -m tracker.main run         # consulta real y actualiza histórico
python -m tracker.main report      # solo regenera dashboard/reporte desde el CSV
```

## Limitaciones a tener en cuenta

- `faster-flights` hace scraping de Google Flights (no es una API oficial). Si Google cambia su página o
  bloquea la IP del runner, esa ejecución falla y verás el error en Actions; la siguiente suele funcionar.
  Si se vuelve recurrente, activar SerpApi da una segunda fuente estable.
- Los precios son **por el total de pasajeros** configurados (`adultos` en `config.yaml`) y en tarifa básica;
  equipaje y selección de silla no están incluidos.
- Clic opera desde Olaya Herrera (EOH); si Google Flights no la indexa para esta ruta, no aparecerá.
