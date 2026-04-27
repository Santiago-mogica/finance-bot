import Groq from "groq-sdk";

const groq = new Groq({ apiKey: process.env.GROQ_API_KEY });

const MODEL = "llama-3.3-70b-versatile";

const SYSTEM_PROMPT = `Sos un asistente financiero personal y de productividad integrado en WhatsApp. 
El usuario te escribe mensajes en español rioplatense (argentino).

Tu tarea es analizar el mensaje y responder SIEMPRE con un JSON válido con esta estructura:

{
  "action": "REGISTER_EXPENSE" | "GET_SUMMARY" | "GET_BY_CATEGORY" | "SET_REMINDER" | "LIST_REMINDERS" | "CHAT" | "HELP",
  "data": { },
  "response": ""
}

REGISTER_EXPENSE:
{ "action": "REGISTER_EXPENSE", "data": { "amount": 1500, "description": "almuerzo", "category": "Comida", "date": "2025-04-26", "currency": "ARS" } }

Categorías: Comida, Transporte, Supermercado, Salud, Entretenimiento, Ropa, Servicios, Alquiler, Tecnología, Educación, Otros

GET_SUMMARY: { "action": "GET_SUMMARY", "data": { "period": "mes" | "semana" | "hoy" } }
GET_BY_CATEGORY: { "action": "GET_BY_CATEGORY", "data": { "category": "Comida" } }
SET_REMINDER: { "action": "SET_REMINDER", "data": { "message": "texto", "datetime": "2025-05-01 09:00", "contact": null, "contact_phone": null } }
LIST_REMINDERS: { "action": "LIST_REMINDERS", "data": {} }
CHAT: { "action": "CHAT", "response": "respuesta en español informal" }
HELP: { "action": "HELP", "data": {} }

REGLAS:
- Respondé SOLO con JSON válido. Sin markdown, sin bloques de código, sin texto fuera del JSON.
- Para montos: "500 pesos", "$500", "quinientos" → 500
- Para fechas relativas: calculá la fecha real desde hoy
- Si falta monto o descripción, usá CHAT y preguntá
- Tono informal argentino en CHAT`;

export async function classifyAndProcess(userId, message, history) {
  try {
    const recentHistory = history.slice(-8).map((msg) => ({
      role: msg.role === "assistant" ? "assistant" : "user",
      content: msg.content,
    }));

    const response = await groq.chat.completions.create({
      model: MODEL,
      messages: [
        {
          role: "system",
          content: SYSTEM_PROMPT + `\n- Hoy es ${new Date().toLocaleDateString("es-AR", { weekday: "long", year: "numeric", month: "long", day: "numeric" })}`,
        },
        ...recentHistory,
      ],
      temperature: 0.1,
      max_tokens: 500,
    });

    const text = response.choices[0].message.content.trim();
    const clean = text.replace(/```json|```/g, "").trim();
    const parsed = JSON.parse(clean);
    return parsed;
  } catch (err) {
    console.error("Error en Groq API:", err.message);
    return {
      action: "CHAT",
      response: "Hubo un problema procesando tu mensaje. ¿Podés repetirlo de otra manera?",
    };
  }
}