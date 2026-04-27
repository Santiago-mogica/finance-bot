import { google } from "googleapis";

// Autenticación con Google Sheets API usando Service Account
function getAuth() {
  const credentials = JSON.parse(process.env.GOOGLE_SERVICE_ACCOUNT_JSON);
  return new google.auth.GoogleAuth({
    credentials,
    scopes: ["https://www.googleapis.com/auth/spreadsheets"],
  });
}

const SPREADSHEET_ID = process.env.GOOGLE_SPREADSHEET_ID;

// Hoja de gastos: columnas A-G
// A: Timestamp | B: UserID | C: Fecha | D: Monto | E: Descripción | F: Categoría | G: Moneda

export async function appendExpense(userId, data) {
  const auth = getAuth();
  const sheets = google.sheets({ version: "v4", auth });

  const row = [
    new Date().toISOString(),     // A: Timestamp registro
    userId,                        // B: UserID (número de WPP)
    data.date,                     // C: Fecha del gasto
    data.amount,                   // D: Monto
    data.description,              // E: Descripción
    data.category,                 // F: Categoría
    data.currency || "ARS",        // G: Moneda
  ];

  await sheets.spreadsheets.values.append({
    spreadsheetId: SPREADSHEET_ID,
    range: "Gastos!A:G",
    valueInputOption: "RAW",
    requestBody: { values: [row] },
  });

  return true;
}

export async function getExpenseSummary(userId, period = "mes") {
  const auth = getAuth();
  const sheets = google.sheets({ version: "v4", auth });

  const response = await sheets.spreadsheets.values.get({
    spreadsheetId: SPREADSHEET_ID,
    range: "Gastos!A:G",
  });

  const rows = response.data.values || [];
  if (rows.length <= 1) return []; // solo header

  const { start, end } = getPeriodDates(period);
  
  // Filtrar por usuario y período
  const filtered = rows.slice(1).filter((row) => {
    const rowUser = row[1];
    const rowDate = new Date(row[2]);
    return rowUser === userId && rowDate >= start && rowDate <= end;
  });

  // Agrupar por categoría
  const byCategory = {};
  filtered.forEach((row) => {
    const category = row[5] || "Otros";
    const amount = parseFloat(row[3]) || 0;
    byCategory[category] = (byCategory[category] || 0) + amount;
  });

  return Object.entries(byCategory).map(([category, total]) => ({
    category,
    total,
  }));
}

export async function getExpensesByCategory(userId, category) {
  const auth = getAuth();
  const sheets = google.sheets({ version: "v4", auth });

  const response = await sheets.spreadsheets.values.get({
    spreadsheetId: SPREADSHEET_ID,
    range: "Gastos!A:G",
  });

  const rows = response.data.values || [];
  if (rows.length <= 1) return [];

  // Filtrar por usuario y categoría (case insensitive)
  const filtered = rows.slice(1).filter((row) => {
    return (
      row[1] === userId &&
      row[5]?.toLowerCase().includes(category.toLowerCase())
    );
  });

  return filtered
    .sort((a, b) => new Date(b[2]) - new Date(a[2])) // más recientes primero
    .slice(0, 20)
    .map((row) => ({
      date: row[2],
      amount: parseFloat(row[3]),
      description: row[4],
      category: row[5],
    }));
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function getPeriodDates(period) {
  const now = new Date();
  let start, end;

  switch (period) {
    case "hoy":
      start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
      end = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 23, 59, 59);
      break;
    case "semana":
      const day = now.getDay();
      const diff = now.getDate() - day + (day === 0 ? -6 : 1);
      start = new Date(now.setDate(diff));
      start.setHours(0, 0, 0, 0);
      end = new Date();
      break;
    case "mes":
    default:
      // Si es "YYYY-MM" específico
      if (period && period.match(/^\d{4}-\d{2}$/)) {
        const [year, month] = period.split("-").map(Number);
        start = new Date(year, month - 1, 1);
        end = new Date(year, month, 0, 23, 59, 59);
      } else {
        start = new Date(now.getFullYear(), now.getMonth(), 1);
        end = new Date(now.getFullYear(), now.getMonth() + 1, 0, 23, 59, 59);
      }
  }

  return { start, end };
}
