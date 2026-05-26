// Deno-based Supabase Edge Function to test direct access to the court portal.

Deno.serve(async (req) => {
  try {
    const targetUrl = "https://bastar.dcourts.gov.in/case-status-search-by-case-type/";
    console.log(`Attempting to fetch ${targetUrl} directly from Edge Function...`);
    
    // Configure a 10-second abort timeout
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 10000);
    
    const response = await fetch(targetUrl, {
      signal: controller.signal,
      headers: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
      }
    });
    
    clearTimeout(timeoutId);
    
    const html = await response.text();
    return new Response(
      JSON.stringify({
        success: true,
        message: "Direct fetch completed successfully (WAF did not block)!",
        status: response.status,
        contentLength: html.length,
        contentSample: html.substring(0, 300)
      }),
      { headers: { "Content-Type": "application/json" } }
    );
  } catch (error) {
    console.error(`Fetch failed: ${error.message}`);
    return new Response(
      JSON.stringify({
        success: false,
        message: "Direct fetch failed or timed out (likely blocked by WAF)",
        error: error.message
      }),
      { status: 500, headers: { "Content-Type": "application/json" } }
    );
  }
});
