#!/usr/bin/env bash
#
# Seed Shopify development store with test products
#
# This script creates the products needed for the integration challenge
# using curl and the Shopify GraphQL Admin API. No dependencies required!
#
# Products are read from scripts/products.csv (single source of truth).
# Each product gets a metafield with its TecovaSuite internal ID.
#
# Usage:
#   ./scripts/seed_shopify.sh           # Create products (skip existing)
#   ./scripts/seed_shopify.sh --reset   # Delete all products, then create
#   ./scripts/seed_shopify.sh --clean   # Delete all products only
#
# Requirements:
#   - .env file with SHOPIFY_STORE_DOMAIN and SHOPIFY_ACCESS_TOKEN
#

set -euo pipefail

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CSV_FILE="${SCRIPT_DIR}/products.csv"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Parse command line arguments
ACTION="seed"
while [[ $# -gt 0 ]]; do
    case $1 in
        --reset)
            ACTION="reset"
            shift
            ;;
        --clean)
            ACTION="clean"
            shift
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --reset   Delete all products, then seed with test products"
            echo "  --clean   Delete all products only (no seeding)"
            echo "  --help    Show this help message"
            echo ""
            echo "Without options, seeds products (skipping any that already exist)"
            echo ""
            echo "Products are loaded from: scripts/products.csv"
            echo "Each product gets a metafield: tecovas.internal_id"
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Check CSV file exists
if [[ ! -f "${CSV_FILE}" ]]; then
    echo -e "${RED}Error: products.csv not found at ${CSV_FILE}${NC}"
    exit 1
fi

# Load environment variables from .env file
if [[ -f .env ]]; then
    export $(grep -v '^#' .env | xargs)
elif [[ -f ../.env ]]; then
    export $(grep -v '^#' ../.env | xargs)
else
    echo -e "${RED}Error: .env file not found${NC}"
    echo "Please create a .env file with SHOPIFY_STORE_DOMAIN and SHOPIFY_ACCESS_TOKEN"
    exit 1
fi

# Validate required environment variables
if [[ -z "${SHOPIFY_STORE_DOMAIN:-}" ]]; then
    echo -e "${RED}Error: SHOPIFY_STORE_DOMAIN not set in .env${NC}"
    echo "Example: your-store.myshopify.com"
    exit 1
fi

if [[ -z "${SHOPIFY_ACCESS_TOKEN:-}" ]]; then
    echo -e "${RED}Error: SHOPIFY_ACCESS_TOKEN not set in .env${NC}"
    echo "Get this from your Shopify app's API credentials"
    exit 1
fi

API_VERSION="2025-10"
GRAPHQL_URL="https://${SHOPIFY_STORE_DOMAIN}/admin/api/${API_VERSION}/graphql.json"

# Metafield configuration
METAFIELD_NAMESPACE="tecovas"
METAFIELD_KEY="internal_id"

# Discount configuration
DISCOUNT_CODE="TEST15"
DISCOUNT_PERCENTAGE=15

echo "============================================================"
echo "Shopify Product Management Script"
echo "============================================================"
echo ""
echo "Store: ${SHOPIFY_STORE_DOMAIN}"
echo "API Version: ${API_VERSION}"
echo "Action: ${ACTION}"
echo "CSV File: ${CSV_FILE}"
echo "Metafield: ${METAFIELD_NAMESPACE}.${METAFIELD_KEY}"
echo "Test Discount: ${DISCOUNT_CODE} (${DISCOUNT_PERCENTAGE}% off)"
echo ""

# Function to make GraphQL request
graphql_request() {
    local query="$1"
    curl -s -X POST "${GRAPHQL_URL}" \
        -H "Content-Type: application/json" \
        -H "X-Shopify-Access-Token: ${SHOPIFY_ACCESS_TOKEN}" \
        -d "{\"query\": \"${query}\"}"
}

# Function to delete a product
delete_product() {
    local product_id="$1"
    local title="$2"

    echo -n "  Deleting: ${title}... "

    local delete_query="mutation { productDelete(input: { id: \\\"${product_id}\\\" }) { deletedProductId userErrors { field message } } }"
    local response=$(graphql_request "${delete_query}")

    if echo "${response}" | grep -q '"deletedProductId"'; then
        echo -e "${GREEN}OK${NC}"
        return 0
    else
        echo -e "${RED}FAILED${NC}"
        return 1
    fi
}

# Function to delete all products
delete_all_products() {
    echo -e "${BLUE}Fetching all products...${NC}"

    local deleted=0
    local failed=0
    local has_more=true

    # Clean up temp file
    rm -f /tmp/shopify_delete_count

    while [[ "${has_more}" == "true" ]]; do
        # Get batch of products
        local query="query { products(first: 50) { edges { node { id title } } pageInfo { hasNextPage } } }"
        local response=$(graphql_request "${query}")

        # Check if there are products
        local products=$(echo "${response}" | grep -oE '"id":"gid://shopify/Product/[0-9]+"' || true)

        if [[ -z "${products}" ]]; then
            has_more=false
            continue
        fi

        # Extract and process each product
        echo "${response}" | grep -oE '"node":\{"id":"gid://shopify/Product/[0-9]+","title":"[^"]*"\}' | while read -r node; do
            local product_id=$(echo "${node}" | grep -oE '"id":"gid://shopify/Product/[0-9]+"' | cut -d'"' -f4)
            local title=$(echo "${node}" | grep -oE '"title":"[^"]+"' | cut -d'"' -f4)

            if [[ -n "${product_id}" ]]; then
                if delete_product "${product_id}" "${title:-Unknown}"; then
                    echo "deleted" >> /tmp/shopify_delete_count
                else
                    echo "failed" >> /tmp/shopify_delete_count
                fi
            fi
        done

        # Check if there are more pages
        if echo "${response}" | grep -q '"hasNextPage":false'; then
            has_more=false
        fi

        # Small delay to avoid rate limiting
        sleep 0.5
    done

    # Count results
    if [[ -f /tmp/shopify_delete_count ]]; then
        deleted=$(grep -c "deleted" /tmp/shopify_delete_count 2>/dev/null || true)
        failed=$(grep -c "failed" /tmp/shopify_delete_count 2>/dev/null || true)
        rm -f /tmp/shopify_delete_count
    fi
    deleted=${deleted:-0}
    failed=${failed:-0}

    echo ""
    echo "Deleted: ${deleted} products"
    if [[ "${failed}" -gt 0 ]]; then
        echo -e "${YELLOW}Failed to delete: ${failed} products${NC}"
    fi

    return 0
}

# Function to create a product with variants and metafield using productSet mutation
create_product() {
    local title="$1"
    local base_sku="$2"
    local price="$3"
    local description="$4"
    local product_type="$5"
    local tecovasuite_start_id="$6"
    local sizes="$7"

    # Build variants based on sizes
    local variants_json=""
    local options_json=""
    local variant_count=0
    local current_id=${tecovasuite_start_id}

    if [[ -n "${sizes}" ]]; then
        # Product has size variants - each variant gets a unique TecovaSuite ID
        options_json="productOptions: [{ name: \\\"Size\\\", values: ["
        variants_json="variants: ["

        # Split sizes by comma
        IFS=',' read -ra size_array <<< "${sizes}"

        local first=true
        for size in "${size_array[@]}"; do
            size="${size// /}"  # Trim whitespace

            if [[ "${first}" == "true" ]]; then
                first=false
            else
                options_json="${options_json}, "
                variants_json="${variants_json}, "
            fi

            options_json="${options_json}{ name: \\\"${size}\\\" }"
            variants_json="${variants_json}{ optionValues: [{ optionName: \\\"Size\\\", name: \\\"${size}\\\" }], sku: \\\"${base_sku}-${size}\\\", price: \\\"${price}\\\", metafields: [{ namespace: \\\"${METAFIELD_NAMESPACE}\\\", key: \\\"${METAFIELD_KEY}\\\", value: \\\"${current_id}\\\", type: \\\"single_line_text_field\\\" }] }"
            ((variant_count++))
            ((current_id++))
        done

        options_json="${options_json}] }]"
        variants_json="${variants_json}]"

        local end_id=$((current_id - 1))
        echo -n "  Creating: ${title} (${base_sku}) [${variant_count} sizes] -> TecovaSuite IDs: ${tecovasuite_start_id}-${end_id}... "
    else
        # No variants - single product
        options_json="productOptions: [{ name: \\\"Title\\\", values: [{ name: \\\"Default Title\\\" }] }]"
        variants_json="variants: [{ optionValues: [{ optionName: \\\"Title\\\", name: \\\"Default Title\\\" }], sku: \\\"${base_sku}\\\", price: \\\"${price}\\\", metafields: [{ namespace: \\\"${METAFIELD_NAMESPACE}\\\", key: \\\"${METAFIELD_KEY}\\\", value: \\\"${tecovasuite_id}\\\", type: \\\"single_line_text_field\\\" }] }]"
        variant_count=1

        echo -n "  Creating: ${title} (${base_sku}) -> TecovaSuite ID: ${tecovasuite_id}... "
    fi

    # Use productSet mutation (metafields are on variants, not product)
    local query="mutation { productSet(input: { title: \\\"${title}\\\", descriptionHtml: \\\"${description}\\\", vendor: \\\"Tecovas\\\", productType: \\\"${product_type}\\\", status: ACTIVE, ${options_json}, ${variants_json} }) { product { id variants(first: 20) { edges { node { id sku } } } } userErrors { field message } } }"

    local response=$(graphql_request "${query}")

    # Check for errors
    local user_errors=$(echo "${response}" | grep -o '"userErrors":\[{[^]]*}\]' || true)
    if [[ -n "${user_errors}" && "${user_errors}" != '"userErrors":[]' ]]; then
        echo -e "${RED}FAILED${NC}"
        echo "    Error: ${user_errors}"
        return 1
    fi

    # Verify product was created and get product ID
    local product_id=$(echo "${response}" | grep -oE '"id":"gid://shopify/Product/[0-9]+"' | head -1 | cut -d'"' -f4)
    if [[ -z "${product_id}" ]]; then
        echo -e "${RED}FAILED${NC}"
        echo "    No product returned"
        return 1
    fi

    # Publish to sales channels if we have publication IDs
    if [[ -n "${PUBLICATION_IDS}" ]]; then
        local publish_query="mutation { publishablePublish(id: \\\"${product_id}\\\", input: [${PUBLICATION_IDS}]) { userErrors { field message } } }"
        local publish_response=$(graphql_request "${publish_query}")
        # Check for publish errors (but don't fail the whole product creation)
        if echo "${publish_response}" | grep -q '"userErrors":\[{'; then
            echo -e "${YELLOW}OK (publish warning)${NC}"
            return 0
        fi
    fi

    echo -e "${GREEN}OK${NC}"
    return 0
}

# Function to seed products from CSV
seed_products() {
    # Check for existing products
    echo "Checking for existing products..."
    existing_query="query { products(first: 100) { edges { node { variants(first: 1) { edges { node { sku } } } } } } }"
    existing_response=$(graphql_request "${existing_query}")
    existing_skus=$(echo "${existing_response}" | grep -oE '"sku":"[^"]*"' | cut -d'"' -f4 | sort -u || echo "")

    echo ""
    echo "Reading products from CSV..."
    echo ""
    echo "Creating products..."

    local created=0
    local skipped=0
    local failed=0
    local line_num=0

    # Read CSV file (pipe-delimited, skip header)
    while IFS='|' read -r title description vendor product_type sku price tecovasuite_id sizes; do
        ((line_num++))

        # Skip header row
        if [[ ${line_num} -eq 1 ]]; then
            continue
        fi

        # Skip empty lines
        if [[ -z "${sku}" ]]; then
            continue
        fi

        # Trim whitespace
        title="${title#"${title%%[![:space:]]*}"}"
        sku="${sku#"${sku%%[![:space:]]*}"}"
        price="${price#"${price%%[![:space:]]*}"}"
        tecovasuite_id="${tecovasuite_id#"${tecovasuite_id%%[![:space:]]*}"}"
        tecovasuite_id="${tecovasuite_id%"${tecovasuite_id##*[![:space:]]}"}"
        sizes="${sizes#"${sizes%%[![:space:]]*}"}"
        sizes="${sizes%"${sizes##*[![:space:]]}"}"

        # Escape description for JSON
        description="${description//\\/\\\\}"
        description="${description//\"/\\\"}"

        # Check if base SKU already exists (check first variant)
        local check_sku="${sku}"
        if [[ -n "${sizes}" ]]; then
            # Get first size for check
            local first_size=$(echo "${sizes}" | cut -d',' -f1 | tr -d ' ')
            check_sku="${sku}-${first_size}"
        fi

        if echo "${existing_skus}" | grep -q "^${check_sku}$"; then
            echo -e "  ${YELLOW}[SKIP]${NC} ${title} (${sku} already exists)"
            ((skipped++))
            continue
        fi

        if create_product "${title}" "${sku}" "${price}" "<p>${description}</p>" "${product_type}" "${tecovasuite_id}" "${sizes}"; then
            ((created++))
        else
            ((failed++))
        fi
    done < "${CSV_FILE}"

    echo ""
    echo "============================================================"
    echo "Summary"
    echo "============================================================"
    echo "  Created: ${created}"
    echo "  Skipped: ${skipped} (already existed)"
    echo "  Failed:  ${failed}"

    if [[ ${created} -gt 0 ]]; then
        echo ""
        echo -e "${GREEN}Products are now available in your Shopify store!${NC}"
        echo "View them at: https://${SHOPIFY_STORE_DOMAIN}/admin/products"
        echo ""
        echo -e "${BLUE}Each product has a metafield:${NC}"
        echo "  Namespace: ${METAFIELD_NAMESPACE}"
        echo "  Key: ${METAFIELD_KEY}"
        echo "  Contains: TecovaSuite internal ID for ERP integration"
    fi

    if [[ ${failed} -gt 0 ]]; then
        echo ""
        echo -e "${YELLOW}Some products failed to create. Check the errors above.${NC}"
        return 1
    fi

    return 0
}

# Function to create a test discount code
create_discount_code() {
    local code="$1"
    local percentage="$2"

    echo "Creating test discount code..."
    echo -n "  Code: ${code} (${percentage}% off)... "

    # Check if discount already exists
    local check_query="query { codeDiscountNodes(first: 50) { edges { node { codeDiscount { ... on DiscountCodeBasic { title } } } } } }"
    local check_response=$(graphql_request "${check_query}")

    if echo "${check_response}" | grep -q "\"title\":\"${code}\""; then
        echo -e "${YELLOW}SKIP (already exists)${NC}"
        return 0
    fi

    # Create discount code using discountCodeBasicCreate mutation
    # Note: percentage as decimal (0.15 = 15%)
    local discount_query="mutation { discountCodeBasicCreate(basicCodeDiscount: { title: \\\"${code}\\\", code: \\\"${code}\\\", startsAt: \\\"2024-01-01T00:00:00Z\\\", customerSelection: { all: true }, customerGets: { value: { percentage: 0.${percentage} }, items: { all: true } } }) { codeDiscountNode { id } userErrors { field message } } }"

    local response=$(graphql_request "${discount_query}")

    # Check for errors first
    local user_errors=$(echo "${response}" | grep -o '"userErrors":\[\{[^]]*\]\]' || echo "")
    if [[ -n "${user_errors}" && "${user_errors}" != '"userErrors":[]' ]]; then
        echo -e "${RED}FAILED${NC}"
        echo "    ${user_errors}"
        echo ""
        echo -e "${YELLOW}Note: You may need to add 'write_discounts' scope to your Shopify app.${NC}"
        echo "Go to: Settings -> Apps and sales channels -> Your app -> Configure Admin API scopes"
        echo ""
        return 1
    fi

    # Verify discount was created
    if echo "${response}" | grep -q '"codeDiscountNode"'; then
        echo -e "${GREEN}OK${NC}"
        echo ""
        echo -e "${BLUE}Test discount code created:${NC}"
        echo "  Code: ${code}"
        echo "  Value: ${percentage}% off entire order"
        echo "  Usage: Enter this code at checkout when creating test orders"
        echo ""
        return 0
    else
        echo -e "${YELLOW}SKIP (may already exist)${NC}"
        return 0
    fi
}

# Test API connection first
echo "Testing API connection..."
test_response=$(graphql_request "{ shop { name } }")
if ! echo "${test_response}" | grep -q '"shop"'; then
    echo -e "${RED}Error: Could not connect to Shopify API${NC}"
    echo "Response: ${test_response}"
    echo ""
    echo "Please check:"
    echo "  1. SHOPIFY_STORE_DOMAIN is correct (without https://)"
    echo "  2. SHOPIFY_ACCESS_TOKEN is valid"
    echo "  3. Your app has the required API scopes"
    exit 1
fi
echo -e "${GREEN}Connected!${NC}"
echo ""

# Ensure metafield definition exists
echo "Checking metafield definition..."
metafield_check=$(graphql_request "{ metafieldDefinitions(ownerType: PRODUCTVARIANT, first: 50) { edges { node { namespace key } } } }")
if ! echo "${metafield_check}" | grep -q "\"namespace\":\"${METAFIELD_NAMESPACE}\".*\"key\":\"${METAFIELD_KEY}\""; then
    echo -n "  Creating metafield definition... "
    create_def_query="mutation { metafieldDefinitionCreate(definition: { name: \\\"TecovaSuite Internal ID\\\", namespace: \\\"${METAFIELD_NAMESPACE}\\\", key: \\\"${METAFIELD_KEY}\\\", type: \\\"single_line_text_field\\\", ownerType: PRODUCTVARIANT, description: \\\"The internal ID used in TecovaSuite for ERP integration\\\" }) { createdDefinition { id } userErrors { field message } } }"
    def_response=$(graphql_request "${create_def_query}")
    if echo "${def_response}" | grep -q '"createdDefinition"'; then
        echo -e "${GREEN}OK${NC}"
    else
        echo -e "${YELLOW}Already exists or skipped${NC}"
    fi
else
    echo -e "  Metafield definition exists ${GREEN}OK${NC}"
fi
echo ""

# Get available sales channels (publications) to publish products to
echo "Fetching sales channels..."
publications_response=$(graphql_request "{ publications(first: 10) { edges { node { id name } } } }")
PUBLICATION_IDS=""

# Extract publication IDs (Online Store, Point of Sale, etc.)
while read -r pub_id; do
    if [[ -n "${pub_id}" ]]; then
        if [[ -n "${PUBLICATION_IDS}" ]]; then
            PUBLICATION_IDS="${PUBLICATION_IDS}, "
        fi
        PUBLICATION_IDS="${PUBLICATION_IDS}{ publicationId: \\\"${pub_id}\\\" }"
    fi
done < <(echo "${publications_response}" | grep -oE '"id":"gid://shopify/Publication/[0-9]+"' | cut -d'"' -f4)

if [[ -n "${PUBLICATION_IDS}" ]]; then
    pub_count=$(echo "${PUBLICATION_IDS}" | grep -o "publicationId" | wc -l | tr -d ' ')
    echo -e "  Found ${pub_count} sales channel(s) ${GREEN}OK${NC}"
else
    echo -e "  ${YELLOW}No sales channels found - products won't be published${NC}"
fi
echo ""

# Execute based on action
case "${ACTION}" in
    clean)
        echo -e "${YELLOW}Deleting all products...${NC}"
        echo ""
        delete_all_products
        echo ""
        echo -e "${GREEN}Store cleaned!${NC}"
        ;;
    reset)
        echo -e "${YELLOW}Resetting store (delete all, then seed)...${NC}"
        echo ""
        delete_all_products
        echo ""
        echo "============================================================"
        echo ""
        create_discount_code "${DISCOUNT_CODE}" "${DISCOUNT_PERCENTAGE}"
        seed_products
        ;;
    seed)
        create_discount_code "${DISCOUNT_CODE}" "${DISCOUNT_PERCENTAGE}"
        seed_products
        ;;
esac
