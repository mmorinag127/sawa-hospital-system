import type { GetServerSideProps } from "next";

export const getServerSideProps: GetServerSideProps = async () => {
  return {
    redirect: {
      destination: "/daily-delivery-notes",
      permanent: false,
    },
  };
};

export default function TotalsRedirectPage() {
  return null;
}
