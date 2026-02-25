import { Connection, LAMPORTS_PER_SOL } from '@solana/web3.js';
import dotenv from 'dotenv';

dotenv.config();

async function testConnection() {
    try {
        const connection = new Connection(
            `https://mainnet.helius-rpc.com/?api-key=${process.env.HELIUS_API_KEY}`,
            'confirmed'
        );
        
        const version = await connection.getVersion();
        console.log('✅ Connected to Solana:', version);
        
        const slot = await connection.getSlot();
        console.log('✅ Current slot:', slot);
        
        console.log('✅ All systems operational!');
    } catch (error) {
        console.error('❌ Connection failed:', error);
    }
}

testConnection();