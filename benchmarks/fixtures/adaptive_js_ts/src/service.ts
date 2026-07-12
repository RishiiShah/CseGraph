import { normalizeName } from "./text";
import { Account } from "./models";
import { saveAccount } from "./store";

export function createAccount(name: string): string {
  const account = new Account(normalizeName(name));
  return saveAccount(account);
}

export function displayAccount(name: string): string {
  return normalizeName(name).toUpperCase();
}
