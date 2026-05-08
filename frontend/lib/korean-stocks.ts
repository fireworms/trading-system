export interface StockItem {
  code: string;
  name: string;
  market: string;   // KOSPI / KOSDAQ / NAS
  country: string;  // KR / US
  sector: string | null;
}
