from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from app.schemas.catalogue import (
    ProductListResponse,
    ProductLookupRequest,
    StompboxProduct,
    SubcategoryCount,
)
from app.schemas.competitor import CompetitorDomain
from app.services.catalogue import Catalogue

router = APIRouter(prefix="/api", tags=["catalogue"])


def get_catalogue(request: Request) -> Catalogue:
    return request.app.state.catalogue


CatalogueDep = Annotated[Catalogue, Depends(get_catalogue)]


@router.get("/catalogue/products", response_model=ProductListResponse)
def list_products(
    catalogue: CatalogueDep,
    q: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
    brand: str | None = None,
) -> ProductListResponse:
    items = catalogue.search(q=q, category=category, subcategory=subcategory, brand=brand)
    return ProductListResponse(total=len(items), items=items)


@router.post("/catalogue/products/lookup", response_model=ProductListResponse)
def lookup_products(body: ProductLookupRequest, catalogue: CatalogueDep) -> ProductListResponse:
    """Resolve a user's product selection. Unknown IDs fail the whole request."""
    found, missing = catalogue.lookup(body.product_ids)
    if missing:
        raise HTTPException(404, detail={"message": "Unknown product IDs", "missing_ids": missing})
    return ProductListResponse(total=len(found), items=found)


@router.get("/catalogue/products/{product_id}", response_model=StompboxProduct)
def get_product(product_id: str, catalogue: CatalogueDep) -> StompboxProduct:
    product = catalogue.products.get(product_id)
    if product is None:
        raise HTTPException(404, detail=f"Product {product_id} not found")
    return product


@router.get("/catalogue/categories", response_model=list[SubcategoryCount])
def list_categories(catalogue: CatalogueDep) -> list[SubcategoryCount]:
    return catalogue.subcategory_counts()


@router.get("/competitors", response_model=list[CompetitorDomain])
def list_competitors(catalogue: CatalogueDep) -> list[CompetitorDomain]:
    return catalogue.competitors
