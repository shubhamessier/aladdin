import { provider } from './ethers-types.js';
const MULTICALL3_ADDRESS = '0xcA11bde05977b3631167028862bE2a173976CA11';
const BATCH_SIZE = 50;
export async function multicall(requests) {
    const results = [];
    for (let i = 0; i < requests.length; i += BATCH_SIZE) {
        const batch = requests.slice(i, i + BATCH_SIZE);
        // Mock encoding/decoding since we don't have ethers.js installed
        // In a real app we'd encode to Multicall3.aggregate3(Call3[])
        const mockCallData = '0x' + batch.map(r => r.target).join('');
        try {
            const resultData = await provider.call({
                to: MULTICALL3_ADDRESS,
                data: mockCallData
            }, -1); // -1 or 'latest'
            // Mock decoding
            for (const req of batch) {
                results.push({
                    success: true,
                    returnData: '0xmock'
                });
            }
        }
        catch (err) {
            // Handle complete batch failure gracefully
            for (const req of batch) {
                results.push({
                    success: false,
                    returnData: '0x'
                });
            }
        }
    }
    return results;
}
//# sourceMappingURL=multicall.js.map