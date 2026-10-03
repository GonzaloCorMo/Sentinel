/**
 * Evalúa fortaleza de contraseña y devuelve detalle para UI de registro.
 *
 * Requisitos mínimos (valid=true): ≥8 chars, mayúscula, minúscula, dígito.
 * Score 0-4 añade símbolo y longitud ≥12 como bonus visual.
 */

export interface PasswordEvaluation {
  score: number; // 0-4
  level: "weak" | "medium" | "strong" | "very-strong";
  label: string;
  valid: boolean; // cumple los 4 requisitos mínimos
  requirements: {
    length: boolean;
    upper: boolean;
    lower: boolean;
    digit: boolean;
    symbol: boolean;
  };
  missingReason: string | null; // mensaje primer requisito no cumplido
}

/** Devuelve score 0-4 + level + label + flags de requisitos + primera razón de fallo. */
export function evaluatePassword(pw: string): PasswordEvaluation {
  const requirements = {
    length: pw.length >= 8,
    upper: /[A-Z]/.test(pw),
    lower: /[a-z]/.test(pw),
    digit: /\d/.test(pw),
    symbol: /[^A-Za-z0-9]/.test(pw),
  };

  // Score 0-4: 1 por length, 1 por upper+lower, 1 por digit, 1 por symbol, +1 si length>=12
  let score = 0;
  if (requirements.length) score++;
  if (requirements.upper && requirements.lower) score++;
  if (requirements.digit) score++;
  if (requirements.symbol) score++;
  if (pw.length >= 12 && score >= 3) score = Math.min(4, score + 0);
  score = Math.min(4, score);

  const level: PasswordEvaluation["level"] =
    score <= 1 ? "weak" : score === 2 ? "medium" : score === 3 ? "strong" : "very-strong";

  const label =
    level === "weak" ? "Débil" : level === "medium" ? "Moderada" : level === "strong" ? "Fuerte" : "Muy fuerte";

  const valid = requirements.length && requirements.upper && requirements.lower && requirements.digit;

  let missingReason: string | null = null;
  if (!requirements.length) missingReason = "Mínimo 8 caracteres.";
  else if (!requirements.upper) missingReason = "Falta una letra mayúscula.";
  else if (!requirements.lower) missingReason = "Falta una letra minúscula.";
  else if (!requirements.digit) missingReason = "Falta un dígito.";

  return { score, level, label, valid, requirements, missingReason };
}
