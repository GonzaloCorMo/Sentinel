import { createRouter, createWebHistory, type RouteLocationNormalized } from "vue-router";
import { getSupabase } from "@/lib/supabase";
import { currentRole, roleHome, type UserRole } from "@/lib/auth";

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: "/login",
      name: "login",
      component: () => import("@/views/LoginView.vue"),
      meta: { public: true },
    },
    {
      path: "/auth/callback",
      name: "auth-callback",
      component: () => import("@/views/AuthCallbackView.vue"),
      meta: { public: true },
    },
    {
      path: "/auth/update-password",
      name: "auth-update-password",
      component: () => import("@/views/AuthUpdatePasswordView.vue"),
      meta: { public: true },
    },
    {
      // Reporte ciudadano. Público (anónimo) — cualquiera puede entrar sin login.
      path: "/message-alert",
      name: "citizen-report",
      component: () => import("@/views/CitizenReportView.vue"),
      meta: { public: true },
    },
    // Alias legacy: /m redirige al nuevo path.
    { path: "/m", redirect: "/message-alert" },
    {
      // Vista de vehículo de emergencia (placeholder fase 2).
      path: "/vehicle",
      name: "vehicle-home",
      component: () => import("@/views/VehicleHomeView.vue"),
      meta: { requiresAuth: true, roles: ["vehicle"] as UserRole[] },
    },
    {
      // Dashboard admin / central — solo administradores.
      path: "/",
      component: () => import("@/layouts/AppShellLayout.vue"),
      meta: { requiresAuth: true, roles: ["admin"] as UserRole[] },
      redirect: "/map",
      children: [
        { path: "map", name: "map", component: () => import("@/views/MapOperationsView.vue") },
        { path: "overview", name: "overview", component: () => import("@/views/RegionOverviewView.vue") },
        { path: "island", redirect: "/overview" },
        { path: "fleet", name: "fleet", component: () => import("@/views/FleetTelemetryView.vue") },
        { path: "comms", name: "comms", component: () => import("@/views/CommsDashboardView.vue") },
        { path: "config", name: "config", component: () => import("@/views/ScenarioConfigView.vue") },
        { path: "reports", name: "reports", component: () => import("@/views/ShiftReportsView.vue") },
      ],
    },
  ],
});

function matchedRoles(to: RouteLocationNormalized): UserRole[] | null {
  for (let i = to.matched.length - 1; i >= 0; i--) {
    const r = to.matched[i].meta?.roles as UserRole[] | undefined;
    if (r && r.length) return r;
  }
  return null;
}

router.beforeEach(async (to) => {
  const sb = getSupabase();
  const sessionData = sb ? await sb.auth.getSession() : null;
  const session = sessionData?.data.session ?? null;
  const role = session ? await currentRole() : null;

  // Ya autenticado y entra a /login → manda a home del rol.
  if (to.name === "login" && session) {
    return roleHome(role);
  }

  // Rutas públicas pasan sin más chequeos.
  if (to.meta.public) return true;

  // Necesita sesión.
  if (to.meta.requiresAuth && !session) {
    return { name: "login", query: { next: to.fullPath } };
  }

  // Restricción por rol.
  const required = matchedRoles(to);
  if (required && (!role || !required.includes(role))) {
    return roleHome(role);
  }

  return true;
});

export default router;
