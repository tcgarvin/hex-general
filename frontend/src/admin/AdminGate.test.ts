import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { MeState } from "@/auth/useMe";

import { AdminGate } from "./AdminGate";

function render(me: MeState): string {
  return renderToStaticMarkup(createElement(AdminGate, { me, children: "ADMIN SCREEN" }));
}

const ready = (isAdmin: boolean): MeState => ({
  status: "ready",
  me: { user_id: "u", is_dev: false, is_admin: isAdmin },
});

describe("AdminGate", () => {
  it("shows the admin screen to an admin", () => {
    const html = render(ready(true));
    expect(html).toContain("ADMIN SCREEN");
    expect(html).not.toContain("not for this account");
  });

  it("shows a plain notice, and not the screen, to anyone else", () => {
    const html = render(ready(false));
    expect(html).toContain("This page is not for this account.");
    expect(html).not.toContain("ADMIN SCREEN");
  });

  it("shows neither while the account loads or when it fails", () => {
    expect(render({ status: "loading" })).not.toContain("ADMIN SCREEN");
    const failed = render({ status: "error", message: "boom" });
    expect(failed).toContain("boom");
    expect(failed).not.toContain("ADMIN SCREEN");
  });
});
