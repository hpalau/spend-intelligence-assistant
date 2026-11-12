const eur = new Intl.NumberFormat("en-GB", { style: "currency", currency: "EUR" });
const plain = new Intl.NumberFormat("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export const fmtEur = (n: number) => eur.format(n);
export const fmtAmount = (n: number, currency: string) => `${currency} ${plain.format(n)}`;
