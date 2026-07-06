export class Account {
  constructor(public name: string) {}

  serialize(): string {
    return `account:${this.name}`;
  }
}

export class Invoice {
  constructor(public number: string) {}

  serialize(): string {
    return `invoice:${this.number}`;
  }
}
