import { dispatch } from "../src/router";

export function testAccountDispatch() {
  if (dispatch("account", { name: "Ada" }) !== "account:ada") throw new Error("account");
}

export function testInvoiceDispatch() {
  if (dispatch("invoice", { number: "I-1" }) !== "invoice:I-1") throw new Error("invoice");
}
