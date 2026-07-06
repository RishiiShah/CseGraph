import { Account } from "./models";

export function saveAccount(account: Account): string {
  return account.serialize();
}

export function loadAccount(name: string): Account {
  return new Account(name);
}
