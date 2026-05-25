import WebSocket from 'ws';
import { EventEmitter } from 'events';

export class HyperliquidWS extends EventEmitter {
    private ws: WebSocket | null = null;
    private pingInterval: NodeJS.Timeout | null = null;
    private reconnectTimeout: NodeJS.Timeout | null = null;
    public isConnected: boolean = false;

    constructor(private readonly isTestnet: boolean = false) {
        super();
    }

    public connect() {
        if (this.ws) {
            this.ws.close();
        }

        const url = this.isTestnet ? 'wss://api.hyperliquid-testnet.xyz/ws' : 'wss://api.hyperliquid.xyz/ws';
        this.ws = new WebSocket(url);

        this.ws.on('open', () => {
            this.isConnected = true;
            console.log(`[WS] Connected to Hyperliquid ${this.isTestnet ? 'Testnet' : 'Mainnet'}`);
            this.emit('connected');
            
            // Subscriptions
            this.subscribeToUserEvents();
            this.subscribeToL2Book('BTC');
            this.subscribeToL2Book('ETH');

            this.pingInterval = setInterval(() => {
                if (this.ws?.readyState === WebSocket.OPEN) {
                    this.ws.send(JSON.stringify({ method: 'ping' }));
                }
            }, 50000);
        });

        this.ws.on('message', (data: WebSocket.RawData) => {
            try {
                const message = JSON.parse(data.toString());
                if (message.channel === 'userEvents') {
                    this.emit('userEvent', message.data);
                } else if (message.channel === 'l2Book') {
                    this.emit('l2Book', message.data);
                }
            } catch (err) {
                console.error('[WS] Error parsing message', err);
            }
        });

        this.ws.on('close', () => {
            this.isConnected = false;
            console.error('[WS] Connection closed. Attempting reconnect...');
            this.emit('disconnected');
            if (this.pingInterval) clearInterval(this.pingInterval);
            
            this.reconnectTimeout = setTimeout(() => {
                this.connect();
            }, 2000); // 2 second backoff
        });

        this.ws.on('error', (err) => {
            console.error(`[WS] Connection error: ${err.message}`);
            this.ws?.close();
        });
    }

    private subscribeToUserEvents() {
        // In production, requires user auth. Abstracted for architecture.
        if (this.ws?.readyState === WebSocket.OPEN) {
            // this.ws.send(JSON.stringify({ method: "subscribe", subscription: { type: "userEvents", user: this.walletAddress } }));
        }
    }

    private subscribeToL2Book(coin: string) {
        if (this.ws?.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ method: "subscribe", subscription: { type: "l2Book", coin } }));
        }
    }

    public disconnect() {
        if (this.pingInterval) clearInterval(this.pingInterval);
        if (this.reconnectTimeout) clearTimeout(this.reconnectTimeout);
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
    }
}
