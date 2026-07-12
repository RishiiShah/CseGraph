import { Invoice } from "./models";

export function handleInvoice(payload) {
  return new Invoice(payload.number).serialize();
}
