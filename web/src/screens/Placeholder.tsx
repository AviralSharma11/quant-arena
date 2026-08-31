import type { RouteDefinition } from "../routes";

/**
 * Every screen is this until its own task builds it.
 *
 * Deliberately plain. Open Issue 014 §11.1 excludes a design system, theming and responsive
 * work beyond not breaking — effort spent on chrome is effort not spent on the panels that
 * constitute the demonstration.
 */
export function Placeholder({ route }: { route: RouteDefinition }) {
  return (
    <section>
      <h1>{route.title}</h1>
      <p>{route.summary}</p>
      <p className="pending">Not built yet — {route.builtBy}.</p>
    </section>
  );
}
