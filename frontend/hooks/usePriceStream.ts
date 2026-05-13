import { useEffect, useRef, useState, useCallback } from "react";

export interface LivePrice {
  current_price: number;
  bid_price: number;
  change: number;
  change_pct: number;
  volume: number;
}

const WS_URL =
  (process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000") + "/ws/prices";

export function usePriceStream(codes: string[]) {
  const [prices, setPrices]     = useState<Record<string, LivePrice>>({});
  const [connected, setConnected] = useState(false);
  const wsRef   = useRef<WebSocket | null>(null);
  const codesRef = useRef<string[]>([]);

  const sendSubscribe = useCallback((ws: WebSocket, list: string[]) => {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "subscribe", codes: list }));
    }
  }, []);

  useEffect(() => {
    if (codes.length === 0) return;
    codesRef.current = codes;

    let reconnectTimer: ReturnType<typeof setTimeout>;
    let active = true;

    function connect() {
      if (!active) return;
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onopen = () => {
        setConnected(true);
        sendSubscribe(ws, codesRef.current);
      };

      ws.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);
          if (data.type === "price") {
            const { code, ...price } = data;
            setPrices((prev) => ({ ...prev, [code]: price as LivePrice }));
          }
        } catch { /* ignore */ }
      };

      ws.onclose = () => {
        setConnected(false);
        if (active) reconnectTimer = setTimeout(connect, 3000);
      };

      ws.onerror = () => ws.close();
    }

    connect();

    // 탭 비활성 시 구독 일시 중단
    const onVisibility = () => {
      if (!wsRef.current) return;
      if (document.hidden) {
        sendSubscribe(wsRef.current, []);
      } else {
        sendSubscribe(wsRef.current, codesRef.current);
      }
    };
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      active = false;
      clearTimeout(reconnectTimer);
      document.removeEventListener("visibilitychange", onVisibility);
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [codes.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps

  // codes 변경 시 재구독
  useEffect(() => {
    codesRef.current = codes;
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      sendSubscribe(wsRef.current, codes);
    }
  }, [codes.join(","), sendSubscribe]); // eslint-disable-line react-hooks/exhaustive-deps

  return { prices, connected };
}
