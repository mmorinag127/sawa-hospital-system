export async function getServerSideProps(context: { params?: { id?: string } }) {
  const orderId = String(context.params?.id || "").trim();
  return {
    redirect: {
      destination: orderId ? `/orders/${encodeURIComponent(orderId)}/workflow-v2` : "/orders",
      permanent: false,
    },
  };
}


export default function OrderRedirectPage() {
  return null;
}
