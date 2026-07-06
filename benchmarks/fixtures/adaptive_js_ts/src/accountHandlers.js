import { createAccount } from "./service";

export function handleAccount(payload) {
  return createAccount(payload.name);
}
