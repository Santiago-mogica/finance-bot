import express from "express";
import { handleIncomingMessage } from "./messageHandler.js";
import { startReminderCron } from "./reminders.js";

const app = express();
app.use(express.urlencoded({ extended: false }));
app.use(express.json());

// Webhook de Twilio
app.post("/webhook", async (req, res) => {
  const from = req.body.From; // ej: "whatsapp:+5491112345678"
  const body = req.body.Body;

  if (!body || !from) {
    return res.status(400).send("Bad request");
  }

  try {
    const reply = await handleIncomingMessage(from, body);
    // Twilio espera TwiML
    const twiml = `<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Message>${reply}</Message>
</Response>`;
    res.type("text/xml").send(twiml);
  } catch (err) {
    console.error("Error:", err);
    res.type("text/xml").send(`<?xml version="1.0" encoding="UTF-8"?>
<Response><Message>Ocurrió un error. Intenta de nuevo.</Message></Response>`);
  }
});

app.get("/", (req, res) => res.send("Bot activo ✅"));

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`Servidor corriendo en puerto ${PORT}`);
  startReminderCron();
});
