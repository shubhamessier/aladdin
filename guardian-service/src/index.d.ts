export interface GuardianState {
    portfolio: any;
    contracts: any;
    initialState: string;
    priceHistory: any;
}
export declare function bootstrap(): Promise<GuardianState>;
export declare function guardianCycle(): Promise<void>;
//# sourceMappingURL=index.d.ts.map