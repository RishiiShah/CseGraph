import { createAccount, displayAccount } from "../src/service";

export function testCreateAccount(): void {
  if (createAccount(" Ada ") !== "account:ada") throw new Error("createAccount");
}

export function testDisplayAccount(): void {
  if (displayAccount(" Ada ") !== "ADA") throw new Error("displayAccount");
}
