import { GoogleGenerativeAI } from "@google/generative-ai";

const genAI = new GoogleGenerativeAI(process.env.GEMINI_API_KEY);

// Gemini 1.5 Flash: gratuito, 1500 requests/día, 1 millón tokens/min
const MODEL = "gemini-1.5-flash-latest";

const SYSTEM_PROMPT = `Sos un asistente financiero personal y de productividad integrado en WhatsApp. 
El usuario te escribe mensajes en español rioplatense (argentino).

Tu tarea es analizar el mensaje y responder SIEMPRE con un JSON válido con esta estructura:

{
  "action": "REGISTER_EXPENSE" | "GET_SUMMARY" | "GET_BY_CATEGORY" | "SET_REMINDER" | "LIST_REMINDERS" | "CHAT" | "HELP",
  "data": { },
  "response": ""
}

=== ACCIONES Y SU "data" ===

REGISTER_EXPENSE — cuando registra un gasto:
{
  "action": "REGISTER_EXPENSE",
  "data": {
    "amount": 1500,
    "description": "almuerzo en McDonald's",
    "category": "Comida",
    "date": "2025-04-26",
    "currency": "ARS"
  }
}

Categorías posibles: Comida, Transporte, Supermercado, Salud, Entretenimiento, Ropa, Servicios, Alquiler, Tecnología, Educación, Otros

GET_SUMMARY — resumen de gastos:
{ "action": "GET_SUMMARY", "data": { "period": "mes" | "semana" | "hoy" | "YYYY-MM" } }

GET_BY_CATEGORY — gastos por categoría:
{ "action": "GET_BY_CATEGORY", "data": { "category": "Comida" } }

SET_REMINDER — programar recordatorio:
{
  "action": "SET_REMINDER",
  "data": {
    "message": "Pagar el alquiler",
    "datetime": "2025-05-01 09:00",
    "contact": null,
    "contact_phone": null
  }
}

LIST_REMINDERS — ver recordatorios:
{ "action": "LIST_REMINDERS", "data": {} }

CHAT — conversación general:
{ "action": "CHAT", "response": "Tu respuesta en español informal" }

HELP — cuando pide ayuda:
{ "action": "HELP", "data": {} }

=== REGLAS ===
- Respondé SOLO con JSON válido. Sin markdown, sin bloques de código, sin texto fuera del JSON.
- Para montos: "500 pesos", "$500", "quinientos" → 500
- Para fechas relativas: calculá la fecha real desde hoy
- Si falta monto o descripción para un gasto, usá CHAT y preguntá
- Tono informal argentino en CHAT`;

export async function classifyAndProcess(userId, message, history) {
  try {
    const model = genAI.getGenerativeModel({
      model: MODEL,
      systemInstruction: SYSTEM_PROMPT + `\n- Hoy es ${new Date().toLocaleDateString("es-AR", { weekday: "long", year: "numeric", month: "long", day: "numeric" })}`,
    });

    // Convertir historial al formato de Gemini (usa "model" en vez de "assistant")
    const recentHistory = history.slice(-8);
    const geminiHistory = [];

    for (let i = 0; i < recentHistory.length - 1; i++) {
      const msg = recentHistory[i];
      geminiHistory.push({
        role: msg.role === "assistant" ? "model" : "user",
        parts: [{ text: msg.content }],
      });
    }

    const chat = model.startChat({ history: geminiHistory });

    const lastMessage = recentHistory[recentHistory.length - 1]?.content || message;
    const result = await chat.sendMessage(lastMessage);
    const text = result.response.text().trim();

    // Limpiar por si Gemini agrega bloques de código igualmente
    const clean = text.replace(/```json|```/g, "").trim();

    const parsed = JSON.parse(clean);
    return parsed;
  } catch (err) {
    console.error("Error en Gemini API:", err.message);
    return {
      action: "CHAT",
      response: "Hubo un problema procesando tu mensaje. ¿Podés repetirlo de otra manera?",
    };
  }
}
