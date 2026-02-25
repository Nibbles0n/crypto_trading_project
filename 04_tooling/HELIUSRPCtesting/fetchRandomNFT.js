async function fetchRandomNFT() {
  const response = await fetch(`https://mainnet.helius-rpc.com/?api-key=19f40d88-796e-4c1e-8ab7-fb0fef99151a`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      jsonrpc: '2.0',
      id: '1',
      method: 'getAssetsByOwner',
      params: {
        ownerAddress: '86xCnPeV69n6t3DnyGvkKobf9FdN2H9oiVDdaMpo2MMY'
      },
    }),
  })

  const data = await response.json()

  if (data.error) {
    console.error('Error fetching NFTs:', data.error.message)
    return
  }
  if (data.result && data.result.items && data.result.items.length > 0) {
    const randomIndex = Math.floor(Math.random() * data.result.items.length)
    const selectedNft = data.result.items[randomIndex]
    console.log(`Id: ${selectedNft.id}`)
    console.log(`Name: ${selectedNft.content?.metadata?.name || 'Unnamed NFT'}`)
    console.log(`Symbol: ${selectedNft.content?.metadata?.symbol || 'N/A'}`)
    console.log(`Image: ${selectedNft.content.files[0].uri || 'No image available'}`)
  }
}

fetchRandomNFT();