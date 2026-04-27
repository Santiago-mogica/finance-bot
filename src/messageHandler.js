import { classifyAndProcess } from "./claude.js";
import { appendExpense, getExpenseSummary, getExpensesByCategory } from "./sheets.js";
import { scheduleReminder, getPendingReminders } from "./reminders.js";

// Contexto de conversación por usuario (en memoria, se pierde al reiniciar)
// Para producción, usar Redis o Sheets como persistencia
const conversationHistory = {};

export async function handleIncomingMessage(from, messageText) {
  const userId = from.replace("whatsapp:", "");

  // Inicializar historial si no existe
  if (!conversationHistory[userId]) {
    conversationHistory[userId] = [];
  }

  // Agregar mensaje del usuario al historial
  conversationHistory[userId].push({
    role: "user",
    content: messageText,
  });

  // Mantener solo los últimos 10 mensajes para no exceder el contexto
  if (conversationHistory[userId].length > 20) {
    conversationHistory[userId] = conversationHistory[userId].slice(-20);
  }

  // Clasificar el mensaje y ejecutar la acción correspondiente
  const result = await classifyAndProcess(
    userId,
    messageText,
    conversationHistory[userId]
  );

  let reply = "";

  switch (result.action) {
    case "REGISTER_EXPENSE":
      await appendExpense(userId, result.data);
      reply = formatExpenseConfirmation(result.data);
      break;

    case "GET_SUMMARY":
      const summary = await getExpenseSummary(userId, result.data.period);
      reply = formatSummary(summary, result.data.period);
      break;

    case "GET_BY_CATEGORY":
      const byCat = await getExpensesByCategory(userId, result.data.category);
      reply = formatCategoryExpenses(byCat, result.data.category);
      break;

    case "SET_REMINDER":
      const reminder = await scheduleReminder(userId, result.data);
      reply = formatReminderConfirmation(result.data);
      break;

    case "LIST_REMINDERS":
      const reminders = await getPendingReminders(userId);
      reply = formatRemindersList(reminders);
      break;

    case "CHAT":
      reply = result.response;
      break;

    case "HELP":
      reply = getHelpMessage();
      break;

    default:
      reply = result.response || "No entendí. Escribí *ayuda* para ver qué puedo hacer.";
  }

  // Agregar respuesta del asistente al historial
  conversationHistory[userId].push({
    role: "assistant",
    content: reply,
  });

  return reply;
}

// ── Formateadores de respuesta ──────────────────────────────────────────────

function formatExpenseConfirmation(data) {
  const emoji = getCategoryEmoji(data.category);
  return `${emoji} *Gasto registrado*
  
💰 Monto: $${data.amount.toLocaleString("es-AR")}
📂 Categoría: ${data.category}
📝 Descripción: ${data.description}
📅 Fecha: ${data.date}

Escribí *resumen* para ver tus gastos del mes.`;
}

function formatSummary(summary, period) {
  if (!summary || summary.length === 0) {
    return `No encontré gastos para ${period || "este período"}.`;
  }

  const total = summary.reduce((sum, item) => sum + item.total, 0);
  const lines = summary
    .sort((a, b) => b.total - a.total)
    .map((item) => `${getCategoryEmoji(item.category)} ${item.category}: $${item.total.toLocaleString("es-AR")}`)
    .join("\n");

  return `📊 *Resumen ${period || "del mes"}*

${lines}

━━━━━━━━━━━━━
💵 *Total: $${total.toLocaleString("es-AR")}*`;
}

function formatCategoryExpenses(expenses, category) {
  if (!expenses || expenses.length === 0) {
    return `No encontré gastos en *${category}*.`;
  }

  const total = expenses.reduce((sum, e) => sum + e.amount, 0);
  const lines = expenses
    .slice(0, 10) // máximo 10 items
    .map((e) => `• ${e.date} — $${e.amount.toLocaleString("es-AR")} — ${e.description}`)
    .join("\n");

  return `${getCategoryEmoji(category)} *Gastos en ${category}*

${lines}

Total: $${total.toLocaleString("es-AR")}`;
}

function formatReminderConfirmation(data) {
  return `⏰ *Recordatorio creado*

📝 ${data.message}
📅 ${data.datetime}${data.contact ? `\n👤 Para: ${data.contact}` : ""}

Te aviso a tiempo! ✅`;
}

function formatRemindersList(reminders) {
  if (!reminders || reminders.length === 0) {
    return "No tenés recordatorios pendientes. ✨";
  }

  const lines = reminders
    .map((r, i) => `${i + 1}. ${r.datetime} — ${r.message}${r.contact ? ` (para ${r.contact})` : ""}`)
    .join("\n");

  return `⏰ *Tus recordatorios pendientes*\n\n${lines}`;
}

function getHelpMessage() {
  return `🤖 *¿Qué puedo hacer por vos?*

💸 *GASTOS*
• "Gasté 500 en taxi"
• "Pagué 1200 de almuerzo"
• "Cargué nafta por 8000"
• "Resumen del mes"
• "Cuánto gasté en comida"
• "Gastos de esta semana"

⏰ *RECORDATORIOS*
• "Recordame pagar el alquiler el 1 de mayo"
• "Avisale a Juan que tiene reunión mañana a las 10"
• "Mis recordatorios"

💬 *CONSULTAS*
• "¿En qué gasté más este mes?"
• "¿Cuánto llevo gastado hoy?"
• Cualquier pregunta sobre tus finanzas

Escribí lo que necesitás de forma natural 😊`;
}

function getCategoryEmoji(category) {
  const map = {
    comida: "🍔",
    restaurante: "🍽️",
    almuerzo: "🍽️",
    cena: "🍽️",
    desayuno: "☕",
    transporte: "🚗",
    taxi: "🚕",
    uber: "🚗",
    nafta: "⛽",
    subte: "🚇",
    supermercado: "🛒",
    salud: "💊",
    farmacia: "💊",
    entretenimiento: "🎬",
    ropa: "👕",
    servicios: "💡",
    alquiler: "🏠",
    tecnología: "💻",
    educación: "📚",
    otros: "📦",
  };

  const key = Object.keys(map).find((k) =>
    category?.toLowerCase().includes(k)
  );
  return map[key] || "💰";
}
