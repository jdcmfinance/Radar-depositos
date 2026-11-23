# RadarDepósitos — prototipo funcional y monitor horario

Este proyecto contiene una página web responsive con comparador de depósitos de bancos de España y Europa, filtros, calculadora aproximada, comparación de tres ofertas, fuentes y registro de variaciones. Incluye una ejecución programada para comprobar periódicamente fuentes públicas. **No se encuentra desplegado: la comprobación horaria comienza solo cuando el propietario conecta y activa GitHub Actions.**

## Probar la web en un ordenador

Desde la carpeta del proyecto:

```bash
python -m http.server 8000
```

Abrir `http://localhost:8000` en el navegador. El archivo `index.html` también incluye una copia de los datos iniciales por si se abre como archivo local, pero los datos renovados se obtienen de `data/offers.json` cuando hay servidor HTTP.

## Publicarla en GitHub Pages (web gratis, con limitaciones)

1. Crea un repositorio nuevo en GitHub y sube **todo el contenido de esta carpeta**, incluida `.github/workflows/check-deposits.yml` (no solo el ZIP).
2. En **Settings → Pages → Build and deployment** selecciona **GitHub Actions** como fuente de publicación (NO "Deploy from a branch"). El workflow publica la web directamente tras cada revisión.
3. En **Actions** habilita las ejecuciones de workflows. Ejecuta manualmente `Revisar depósitos y publicar la web (cada hora)` usando **Run workflow** para verificar permisos y conexiones. El siguiente chequeo programado se intentará al minuto 13 de cada hora (los cron de GitHub pueden retrasarse o saltarse ejecuciones).
4. En **Settings → Actions → General → Workflow permissions** permite escritura a `GITHUB_TOKEN` si tu repositorio no lo permite. La acción actualiza el archivo de ofertas mediante commits de Git y **publica directamente la web con `actions/deploy-pages`**. Los commits realizados con `GITHUB_TOKEN` no activan por sí solos la publicación basada en rama.
5. Abre la URL de GitHub Pages que muestre la sección Pages.

GitHub Pages es un sitio estático: no aloja el proceso Python. El agente se ejecuta temporalmente con GitHub Actions, guarda datos y publica una nueva versión del sitio. La ejecución programada de GitHub no es un reloj exacto; puede retrasarse o saltarse algún ciclo, y GitHub puede desactivar workflows de repositorios públicos sin actividad prolongada.

## Activar extracción con IA (opcional)

En **Settings → Secrets and variables → Actions** añade `OPENAI_API_KEY` con una clave de la **API de OpenAI**. ChatGPT Plus y la API son servicios facturados por separado. Sin esta clave, el proceso sigue revisando reglas verificables para HoistSpar y Renault Bank; la búsqueda semántica en el catálogo de Raisin queda desactivada.

La IA genera `data/pending.json` con candidatos detectados. **Nunca publica directamente una nueva oferta ni una gran variación:** el operador debe contrastar mínimo, garantía, cancelación, vigencia y página original antes de añadirla a `data/offers.json`. Las variaciones normales de la TAE de productos existentes y detectadas inequívocamente en las páginas oficiales de HoistSpar y Renault Bank sí se actualizan automáticamente.

## Activar alertas por correo (opcional)

Añade secretos de Actions: `SMTP_HOST`, `SMTP_PORT` (por defecto 465), `SMTP_USER`, `SMTP_PASSWORD`, `ALERT_EMAIL`. El script envía un aviso al propietario cuando detecta un cambio de TAE en las fuentes primarias. Para enviar alertas a suscriptores públicos se necesitaría integrar un proveedor de correo con registro, opt-in y baja conforme a normativa de protección de datos.

## Qué revisa, y qué no

- **Verificación automática primaria:** Renault Bank (`renaultbank.es`) y HoistSpar (`hoistspar.es`): plazos y rentabilidades publicados.
- **Descubrimiento opcional con IA:** ofertas de Raisin (`raisin.com`): solo propuestas de revisión, no auto-publicadas.
- **Observación sin extracción fiable automática:** MyInvestor (`myinvestor.es`), cuyo sitio puede bloquear automatismos.
- **La primera carga contiene 14 depósitos** documentados con fuentes oficiales o comunicaciones del distribuidor, vistos el 23/11/2026. No equivale al ranking exhaustivo del mercado.
- El proceso guarda `data/monitor_state.json` con errores por fuente. Una web bloqueada, caída o con formato cambiado NO es una prueba de que la oferta desapareció.
- El código no abre cuentas bancarias, no recibe contraseñas de bancos, no mueve fondos y no publica alertas de disponibilidad de depósitos sin evidencia comprobable.

## Cómo ampliar el proyecto profesionalmente

Añadir conectores oficiales por entidad y país, detección de ofertas nuevas mediante buscadores autorizados, colas de revisión, banco de datos SQLite/PostgreSQL, histórico de tasas, términos completos, un backend de alertas con doble consentimiento y panel de administración. Supervisar `robots.txt` y condiciones de uso de cada web y preferir APIs/licencias de datos si la cobertura pasa a ser comercial.

## Modelo de datos

`data/offers.json` almacena entidad, producto, meses, TAE, TIN, mínimo, máximo, país, fondo de garantía, cancelación, liquidación, requisitos, fin de promoción, URL exacta, fecha observada y eventos de cambio.

**Nota financiera:** la TAE se usa para ordenar; el cálculo bruto ilustrativo aplica el TIN cuando está disponible (sin reinversión) y, en caso contrario, una aproximación mediante TAE. Las cifras netas son una estimación usando tramos de base del ahorro, no una liquidación fiscal personalizada. Los límites de garantía se refieren a entidades y titulares, no a cada depósito individual.
