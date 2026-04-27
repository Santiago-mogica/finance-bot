import { google } from "googleapis";
import twilio from "twilio";
import cron from "node-cron";

function getAuth() {
  const credentials = JSON.parse(process.env.GOOGLE_SERVICE_ACCOUNT_JSON);
  return new google.auth.GoogleAuth({
    credentials,
    scopes: ["https://www.googleapis.com/auth/spreadsheets"],
  });
}

const SPREADSHEET_ID = process.env.GOOGLE_SPREADSHEET_ID;

// Hoja Recordatorios: A: ID | B: UserID | C: Datetime | D: Mensaje | E: Contacto | F: ContactPhone | G: Status

export async function scheduleReminder(userId, data) {
  const auth = getAuth();
  const sheets = google.sheets({ version: "v4", auth });

  const id = `R${Date.now()}`;
  const row = [
    id,
    userId,
    data.datetime,
    data.message,
    data.contact || "",
    data.contact_phone || "",
    "PENDING",
  ];

  await sheets.spreadsheets.values.append({
    spreadsheetId: SPREADSHEET_ID,
    range: "Recordatorios!A:G",
    valueInputOption: "RAW",
    requestBody: { values: [row] },
  });

  return { id, ...data };
}

export async function getPendingReminders(userId) {
  const auth = getAuth();
  const sheets = google.sheets({ version: "v4", auth });

  const response = await sheets.spreadsheets.values.get({
    spreadsheetId: SPREADSHEET_ID,
    range: "Recordatorios!A:G",
  });

  const rows = response.data.values || [];
  const now = new Date();

  return rows.slice(1)
    .filter((row) => row[1] === userId && row[6] === "PENDING" && new Date(row[2]) > now)
    .map((row) => ({
      id: row[0],
      datetime: row[2],
      message: row[3],
      contact: row[4] || null,
    }))
    .sort((a, b) => new Date(a.datetime) - new Date(b.datetime))
    .slice(0, 10);
}

// ── Cron job: revisar recordatorios cada minuto ──────────────────────────────

export function startReminderCron() {
  const twilioClient = twilio(
    process.env.TWILIO_ACCOUNT_SID,
    process.env.TWILIO_AUTH_TOKEN
  );

  cron.schedule("* * * * *", async () => {
    try {
      const auth = getAuth();
      const sheets = google.sheets({ version: "v4", auth });

      const response = await sheets.spreadsheets.values.get({
        spreadsheetId: SPREADSHEET_ID,
        range: "Recordatorios!A:G",
      });

      const rows = response.data.values || [];
      if (rows.length <= 1) return;

      const now = new Date();
      const fiveMinutesAgo = new Date(now - 5 * 60 * 1000);

      for (let i = 1; i < rows.length; i++) {
        const row = rows[i];
        const reminderTime = new Date(row[2]);
        const status = row[6];

        // Disparar si está pendiente y la hora ya pasó (con ventana de 5 min)
        if (
          status === "PENDING" &&
          reminderTime >= fiveMinutesAgo &&
          reminderTime <= now
        ) {
          const userId = row[1]; // número de WPP del dueño del bot
          const message = row[3];
          const contact = row[4];
          const contactPhone = row[5];

          // Enviar recordatorio al usuario principal
          const userMessage = contact
            ? `⏰ *Recordatorio*: ${message}\n👤 Para enviar a: ${contact}${contactPhone ? ` (${contactPhone})` : ""}`
            : `⏰ *Recordatorio*: ${message}`;

          await twilioClient.messages.create({
            from: `whatsapp:${process.env.TWILIO_PHONE_NUMBER}`,
            to: `whatsapp:${userId}`,
            body: userMessage,
          });

          // Si tiene contacto con teléfono, también enviarle a él
          if (contactPhone) {
            await twilioClient.messages.create({
              from: `whatsapp:${process.env.TWILIO_PHONE_NUMBER}`,
              to: `whatsapp:${contactPhone}`,
              body: `👋 Hola! Te manda un recordatorio: ${message}`,
            });
          }

          // Marcar como enviado
          await sheets.spreadsheets.values.update({
            spreadsheetId: SPREADSHEET_ID,
            range: `Recordatorios!G${i + 1}`,
            valueInputOption: "RAW",
            requestBody: { values: [["SENT"]] },
          });

          console.log(`Recordatorio enviado: ${message}`);
        }
      }
    } catch (err) {
      console.error("Error en cron de recordatorios:", err);
    }
  });

  console.log("✅ Cron de recordatorios activo");
}
