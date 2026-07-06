import { handleAccount } from "./accountHandlers";
import { handleInvoice } from "./invoiceHandlers";

const handlers = {
  account: handleAccount,
  invoice: handleInvoice,
};

export function dispatch(kind, payload) {
  return handlers[kind](payload);
}
