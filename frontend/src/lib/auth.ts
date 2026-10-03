/**
 * Utilidades de rol de usuario.
 *
 * El rol se almacena en `user_metadata.role` de Supabase Auth y determina
 * la ruta home a la que redirige el router tras el login. Sin metadata
 * el usuario se trata como `citizen` (comportamiento más restrictivo).
 */
import { getSupabase } from "./supabase";

export type UserRole = "citizen" | "admin" | "vehicle";
export const ROLES: UserRole[] = ["citizen", "admin", "vehicle"];

export const ROLE_LABELS: Record<UserRole, string> = {
  citizen: "Ciudadano",
  admin: "Administrador / Central",
  vehicle: "Vehículo de emergencia",
};

export const ROLE_HOME: Record<UserRole, string> = {
  citizen: "/message-alert",
  admin: "/map",
  vehicle: "/vehicle",
};

/** Rol actual del usuario autenticado (o `null` si no hay sesión). */
export async function currentRole(): Promise<UserRole | null> {
  const sb = getSupabase();
  if (!sb) return null;
  const { data } = await sb.auth.getSession();
  const session = data.session;
  if (!session) return null;
  const raw = session.user?.user_metadata?.role as string | undefined;
  if (raw && (ROLES as string[]).includes(raw)) return raw as UserRole;
  // Fallback: usuarios sin metadata explícita tratados como citizen.
  return "citizen";
}

/** Devuelve la ruta home para un rol (o `/login` si el usuario no autenticado). */
export function roleHome(role: UserRole | null): string {
  if (!role) return "/login";
  return ROLE_HOME[role];
}
