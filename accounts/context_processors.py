from accounts.models import User


def active_branch(request):
    if not request.user.is_authenticated or not request.user.is_admin_role:
        return {}
    if "branch" in request.GET:
        branch = request.GET.get("branch", "").strip()
        request.session["active_branch"] = branch
    return {
        "active_branch": request.session.get("active_branch", ""),
        "branches": User.Branch.choices,
    }
