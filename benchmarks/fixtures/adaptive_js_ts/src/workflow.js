import { dispatch } from "./router";
import { displayAccount } from "./service";

export function runOnboarding(name) {
  const stored = dispatch("account", { name });
  return [stored, displayAccount(name)];
}

export function runInvoice(number) {
  return dispatch("invoice", { number });
}
