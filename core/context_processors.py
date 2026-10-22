from .models import BoxConfiguration, Category, EventOccasion


def cart_and_site_context(request):
    """Provides global context: cart item count, active occasions, categories, and admin status."""
    cart_count = 0
    try:
        if request.user.is_authenticated:
            cart_count = sum(item.quantity for item in BoxConfiguration.objects.filter(
                user=request.user, status=BoxConfiguration.CART,
            ))
        else:
            # ensure session exists but don't spam saves
            session_key = request.session.session_key
            if not session_key:
                try:
                    request.session.save()
                    session_key = request.session.session_key
                except Exception:
                    session_key = None
            if session_key:
                cart_count = sum(item.quantity for item in BoxConfiguration.objects.filter(
                    session_key=session_key, status=BoxConfiguration.CART,
                ))
    except Exception:
        cart_count = 0

    try:
        occasions = EventOccasion.objects.filter(is_active=True).order_by('name')
        categories = Category.objects.filter(is_active=True)
    except Exception:
        occasions = []
        categories = []

    return {
        'cart_count': cart_count,
        'nav_occasions': occasions,
        'nav_categories': categories,
        'is_admin_user': request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser),
    }
