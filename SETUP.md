# 🤖 Bot de WhatsApp — Guía de Setup Completa

## ¿Qué hace este bot?

- 💸 **Registra gastos** con lenguaje natural ("gasté 500 en pizza")
- 📊 **Resume tus gastos** por período o categoría
- ⏰ **Recordatorios** para vos o para contactos
- 💬 **Asistente financiero** que responde preguntas sobre tus finanzas

---

## PASO 1 — Clonar y preparar el proyecto

```bash
git clone <tu-repo>
cd whatsapp-finance-bot
npm install
cp .env.example .env
```

---

## PASO 2 — Configurar Twilio (WhatsApp Sandbox)

1. Ir a [twilio.com](https://www.twilio.com) → crear cuenta gratis
2. En el dashboard: **Messaging → Try it out → Send a WhatsApp message**
3. Te va a dar un número de sandbox (ej: +1 415 523 8886)
4. Desde tu WhatsApp, mandar el mensaje que te pide (ej: "join silver-tiger") al número
5. Copiar al `.env`:
   - `TWILIO_ACCOUNT_SID` → Account Info en el dashboard
   - `TWILIO_AUTH_TOKEN` → Account Info en el dashboard
   - `TWILIO_PHONE_NUMBER` → el número del sandbox

---

## PASO 3 — Configurar Google Sheets

### 3.1 Crear la planilla
1. Ir a [sheets.google.com](https://sheets.google.com) → crear nueva planilla
2. Renombrarla "Finance Bot"
3. Crear dos hojas (tabs):
   - `Gastos` — con estos headers en fila 1:
     `Timestamp | UserID | Fecha | Monto | Descripcion | Categoria | Moneda`
   - `Recordatorios` — con estos headers en fila 1:
     `ID | UserID | Datetime | Mensaje | Contacto | ContactoPhone | Status`
4. Copiar el ID de la URL (la parte larga entre /d/ y /edit) → `GOOGLE_SPREADSHEET_ID`

### 3.2 Crear Service Account
1. Ir a [console.cloud.google.com](https://console.cloud.google.com)
2. Crear proyecto nuevo (o usar uno existente)
3. Activar **Google Sheets API**: APIs & Services → Enable APIs → buscar "Google Sheets API"
4. Crear credenciales: APIs & Services → Credentials → Create Credentials → **Service Account**
5. Completar nombre, continuar
6. En la service account creada → Keys → Add Key → JSON → descargar
7. Abrir el JSON descargado y copiarlo completo como valor de `GOOGLE_SERVICE_ACCOUNT_JSON`

### 3.3 Compartir la planilla con el Service Account
1. Abrir el JSON y copiar el campo `client_email` (algo como `bot@proyecto.iam.gserviceaccount.com`)
2. En Google Sheets → botón Compartir → pegar ese email → darle permiso de **Editor**

---

## PASO 4 — Configurar Claude API

1. Ir a [console.anthropic.com](https://console.anthropic.com)
2. API Keys → Create Key
3. Copiar al `.env` como `ANTHROPIC_API_KEY`

---

## PASO 5 — Deploy en Railway

1. Subir el código a GitHub:
```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/tu-usuario/tu-repo.git
git push -u origin main
```

2. Ir a [railway.app](https://railway.app) → Sign in with GitHub
3. **New Project → Deploy from GitHub repo** → seleccionar tu repo
4. En el proyecto: **Variables** → agregar todas las variables del `.env`
5. Railway te da una URL pública (ej: `https://tu-bot.up.railway.app`)

---

## PASO 6 — Conectar Twilio con Railway

1. Copiar tu URL de Railway
2. En Twilio → Messaging → Settings → WhatsApp Sandbox Settings
3. En "When a message comes in": pegar `https://tu-bot.up.railway.app/webhook`
4. Método: **HTTP POST**
5. Guardar

---

## PASO 7 — Probar

Mandá desde WhatsApp (con el sandbox conectado):
- `ayuda` → ver todos los comandos
- `gasté 500 en almuerzo` → registrar gasto
- `resumen del mes` → ver gastos
- `recordame pagar el alquiler el 1 de mayo a las 9` → recordatorio

---

## 💡 Tips

- **Sandbox limitation**: En el sandbox de Twilio, solo vos (y quien haya activado el sandbox) puede recibir mensajes. Para producción, necesitás aprobar el número con Meta (proceso gratis pero lleva unos días).
- **Costos estimados**:
  - Twilio Sandbox: gratis para desarrollo
  - Railway: free tier = ~500hs/mes (suficiente para uso personal)
  - Claude API: ~$0.003 por mensaje (muy barato)
  - Google Sheets API: gratis

---

## 🚀 Upgrades posibles (futuro)

- [ ] Dashboard web para ver gastos con gráficos
- [ ] Exportar a Excel
- [ ] Alertas cuando superás un presupuesto mensual
- [ ] Gastos compartidos con tu pareja/compañero
- [ ] Fotos de tickets/facturas procesadas con OCR
- [ ] Integración con tu banco (scraping o open banking)
