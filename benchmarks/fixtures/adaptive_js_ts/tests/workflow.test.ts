import { runInvoice, runOnboarding } from "../src/workflow";

export function testOnboarding(): void {
  const result = runOnboarding(" Ada ");
  if (result[0] !== "account:ada" || result[1] !== "ADA") throw new Error("onboarding");
}

export function testInvoice(): void {
  if (runInvoice("I-1") !== "invoice:I-1") throw new Error("invoice");
}
