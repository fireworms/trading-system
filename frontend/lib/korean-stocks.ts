export interface StockItem {
  code: string;
  name: string;
  market: "KOSPI" | "KOSDAQ";
  sector: string;
}

// 주요 KOSPI / KOSDAQ 종목 (섹터별)
export const KOREAN_STOCKS: StockItem[] = [
  // 반도체/전자
  { code: "005930", name: "삼성전자",        market: "KOSPI",  sector: "반도체/전자" },
  { code: "000660", name: "SK하이닉스",       market: "KOSPI",  sector: "반도체" },
  { code: "042700", name: "한미반도체",        market: "KOSPI",  sector: "반도체 장비" },
  { code: "091990", name: "셀트리온헬스케어",  market: "KOSDAQ", sector: "바이오" },
  { code: "005935", name: "삼성전자우",        market: "KOSPI",  sector: "반도체/전자" },
  { code: "000990", name: "DB하이텍",          market: "KOSPI",  sector: "반도체" },
  { code: "023770", name: "플레이디",          market: "KOSDAQ", sector: "IT서비스" },
  { code: "240810", name: "원익IPS",           market: "KOSDAQ", sector: "반도체 장비" },
  // 인터넷/플랫폼
  { code: "035420", name: "NAVER",             market: "KOSPI",  sector: "인터넷/플랫폼" },
  { code: "035720", name: "카카오",            market: "KOSPI",  sector: "인터넷/플랫폼" },
  { code: "259960", name: "크래프톤",          market: "KOSPI",  sector: "게임" },
  { code: "036570", name: "NCsoft",            market: "KOSPI",  sector: "게임" },
  { code: "251270", name: "넷마블",            market: "KOSPI",  sector: "게임" },
  { code: "263750", name: "펄어비스",          market: "KOSDAQ", sector: "게임" },
  // 배터리/전기차
  { code: "006400", name: "삼성SDI",           market: "KOSPI",  sector: "배터리" },
  { code: "051910", name: "LG화학",            market: "KOSPI",  sector: "화학/배터리" },
  { code: "373220", name: "LG에너지솔루션",    market: "KOSPI",  sector: "배터리" },
  { code: "247540", name: "에코프로비엠",       market: "KOSDAQ", sector: "배터리소재" },
  { code: "086520", name: "에코프로",          market: "KOSDAQ", sector: "배터리소재" },
  { code: "096770", name: "SK이노베이션",      market: "KOSPI",  sector: "에너지/화학" },
  { code: "011780", name: "금호석유",          market: "KOSPI",  sector: "화학" },
  // 바이오/제약
  { code: "068270", name: "셀트리온",          market: "KOSPI",  sector: "바이오" },
  { code: "207940", name: "삼성바이오로직스",  market: "KOSPI",  sector: "바이오" },
  { code: "128940", name: "한미약품",          market: "KOSPI",  sector: "제약" },
  { code: "326030", name: "SK바이오팜",        market: "KOSPI",  sector: "바이오" },
  { code: "145020", name: "휴젤",              market: "KOSDAQ", sector: "바이오" },
  { code: "196170", name: "알테오젠",          market: "KOSDAQ", sector: "바이오" },
  { code: "293490", name: "카카오게임즈",      market: "KOSDAQ", sector: "게임" },
  // 자동차
  { code: "005380", name: "현대차",            market: "KOSPI",  sector: "자동차" },
  { code: "000270", name: "기아",              market: "KOSPI",  sector: "자동차" },
  { code: "012330", name: "현대모비스",        market: "KOSPI",  sector: "자동차부품" },
  { code: "011210", name: "현대위아",          market: "KOSPI",  sector: "자동차부품" },
  { code: "161390", name: "한국타이어앤테크놀로지", market: "KOSPI", sector: "자동차부품" },
  // 금융
  { code: "086790", name: "하나금융지주",      market: "KOSPI",  sector: "금융" },
  { code: "055550", name: "신한지주",          market: "KOSPI",  sector: "금융" },
  { code: "105560", name: "KB금융",            market: "KOSPI",  sector: "금융" },
  { code: "316140", name: "우리금융지주",      market: "KOSPI",  sector: "금융" },
  { code: "024110", name: "기업은행",          market: "KOSPI",  sector: "금융" },
  { code: "032830", name: "삼성생명",          market: "KOSPI",  sector: "보험" },
  { code: "000810", name: "삼성화재",          market: "KOSPI",  sector: "보험" },
  // 통신
  { code: "017670", name: "SK텔레콤",          market: "KOSPI",  sector: "통신" },
  { code: "030200", name: "KT",                market: "KOSPI",  sector: "통신" },
  { code: "032640", name: "LG유플러스",        market: "KOSPI",  sector: "통신" },
  // 지주/건설
  { code: "003550", name: "LG",               market: "KOSPI",  sector: "지주" },
  { code: "034730", name: "SK",               market: "KOSPI",  sector: "지주" },
  { code: "028260", name: "삼성물산",          market: "KOSPI",  sector: "건설/지주" },
  { code: "000720", name: "현대건설",          market: "KOSPI",  sector: "건설" },
  { code: "006360", name: "GS건설",           market: "KOSPI",  sector: "건설" },
  { code: "047040", name: "대우건설",          market: "KOSPI",  sector: "건설" },
  // 조선/중공업
  { code: "009540", name: "HD한국조선해양",   market: "KOSPI",  sector: "조선" },
  { code: "329180", name: "HD현대중공업",     market: "KOSPI",  sector: "조선" },
  { code: "010140", name: "삼성중공업",        market: "KOSPI",  sector: "조선" },
  { code: "042660", name: "한화오션",          market: "KOSPI",  sector: "조선" },
  // 에너지/정유
  { code: "010950", name: "S-Oil",             market: "KOSPI",  sector: "정유" },
  { code: "078930", name: "GS",               market: "KOSPI",  sector: "에너지/지주" },
  { code: "267250", name: "HD현대",            market: "KOSPI",  sector: "지주" },
  // 철강/소재
  { code: "005490", name: "POSCO홀딩스",      market: "KOSPI",  sector: "철강" },
  { code: "004020", name: "현대제철",          market: "KOSPI",  sector: "철강" },
  { code: "010060", name: "OCI홀딩스",        market: "KOSPI",  sector: "화학" },
  // 유통/소비재
  { code: "139480", name: "이마트",           market: "KOSPI",  sector: "유통" },
  { code: "004170", name: "신세계",           market: "KOSPI",  sector: "유통" },
  { code: "069960", name: "현대백화점",        market: "KOSPI",  sector: "유통" },
  { code: "033600", name: "롯데쇼핑",          market: "KOSPI",  sector: "유통" },
  { code: "000080", name: "하이트진로",        market: "KOSPI",  sector: "음식료" },
  { code: "097950", name: "CJ제일제당",        market: "KOSPI",  sector: "음식료" },
  // 항공/운송
  { code: "003490", name: "대한항공",          market: "KOSPI",  sector: "항공" },
  { code: "020560", name: "아시아나항공",      market: "KOSPI",  sector: "항공" },
  // KOSDAQ 주요 종목
  { code: "041510", name: "에스엠",           market: "KOSDAQ", sector: "엔터" },
  { code: "035900", name: "JYP Ent.",          market: "KOSDAQ", sector: "엔터" },
  { code: "122870", name: "와이지엔터테인먼트", market: "KOSDAQ", sector: "엔터" },
  { code: "357780", name: "솔브레인",          market: "KOSDAQ", sector: "반도체소재" },
  { code: "036800", name: "나스미디어",        market: "KOSDAQ", sector: "IT서비스" },
  { code: "095660", name: "네오위즈",          market: "KOSDAQ", sector: "게임" },
  { code: "067310", name: "하나마이크론",       market: "KOSDAQ", sector: "반도체" },
  { code: "140860", name: "파크시스템스",      market: "KOSDAQ", sector: "반도체 장비" },
  { code: "214150", name: "클래시스",          market: "KOSDAQ", sector: "의료기기" },
  { code: "039030", name: "이오테크닉스",      market: "KOSDAQ", sector: "반도체 장비" },
  { code: "112040", name: "위메이드",          market: "KOSDAQ", sector: "게임" },
  { code: "348210", name: "넥스틴",           market: "KOSDAQ", sector: "반도체 장비" },
];

export function searchStocks(query: string): StockItem[] {
  if (!query.trim()) return [];
  const q = query.trim().toLowerCase();
  return KOREAN_STOCKS.filter(
    (s) => s.code.startsWith(q) || s.name.toLowerCase().includes(q)
  ).slice(0, 10);
}
