import {
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { AppShell } from "@/components/AppShell";
import { DashboardPage } from "@/features/dashboard/DashboardPage";
import { ExplorerPage } from "@/features/explorer/ExplorerPage";
import { ValidationPage } from "@/features/validation/ValidationPage";

const rootRoute = createRootRoute({ component: AppShell });

const dashboardRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: DashboardPage,
});

const explorerRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/explorer",
  component: ExplorerPage,
});

const validationRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/validation",
  component: ValidationPage,
});

const routeTree = rootRoute.addChildren([
  dashboardRoute,
  explorerRoute,
  validationRoute,
]);

export const router = createRouter({ routeTree, defaultPreload: "intent" });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
